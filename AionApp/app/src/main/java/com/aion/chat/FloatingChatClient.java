package com.aion.chat;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import org.json.JSONObject;
import java.io.IOException;
import java.util.Iterator;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import okhttp3.*;

/** Uses the same authenticated send endpoints as the WebView, including SSE. */
final class FloatingChatClient {
    private final SharedPreferences prefs;
    private final OkHttpClient client = new OkHttpClient.Builder()
            .cookieJar(new WebViewCookieJar()).connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS).build();
    private volatile Call sending;
    private volatile boolean closed;
    private final String clientId;

    FloatingChatClient(Context context) {
        prefs = context.getSharedPreferences("aion_prefs", Context.MODE_PRIVATE);
        String savedId = prefs.getString("floating_client_id", "");
        clientId = savedId.isEmpty() ? UUID.randomUUID().toString() : savedId;
        prefs.edit().putString("floating_client_id", clientId).apply();
    }

    private HttpUrl url(String path) throws IOException {
        HttpUrl base = HttpUrl.parse(prefs.getString("saved_url", ""));
        if (base == null) throw new IOException("请先打开小家选择连接地址");
        HttpUrl resolved = base.resolve(path);
        if (resolved == null || !base.host().equals(resolved.host())) throw new IOException("连接地址无效");
        return resolved;
    }

    JSONObject context() throws Exception {
        return json(new Request.Builder().url(url("/api/floating-chat/context")).build());
    }

    String screen() throws Exception {
        JSONObject body = new JSONObject().put("client_id", clientId);
        return json(new Request.Builder().url(url("/api/floating-chat/screen"))
                .post(RequestBody.create(body.toString(), MediaType.get("application/json"))).build())
                .getString("attachment");
    }

    boolean acceptsScreenRequest(JSONObject data) {
        return data != null && clientId.equals(data.optString("client_id"));
    }

    Bitmap avatar(String path) {
        try (Response response = client.newCall(new Request.Builder().url(url(path)).build()).execute()) {
            if (!response.isSuccessful() || response.body() == null) return null;
            return BitmapFactory.decodeStream(response.body().byteStream());
        } catch (Exception ignored) { return null; }
    }

    private JSONObject json(Request request) throws Exception {
        return new JSONObject(readJson(request));
    }

    private String readJson(Request request) throws Exception {
        try (Response response = client.newCall(request).execute()) {
            if (response.body() == null) throw new IOException("没有收到服务器响应");
            String body = response.body().string();
            if (!response.isSuccessful()) {
                String detail = "连接失败（" + response.code() + "）";
                try { detail = new JSONObject(body).optString("detail", detail); } catch (Exception ignored) {}
                throw new IOException(detail);
            }
            return body;
        }
    }

    org.json.JSONArray history(JSONObject context) throws Exception {
        String path = context.getString("send_url").replaceFirst("/send$", "/messages") + "?limit=100";
        return new org.json.JSONArray(readJson(new Request.Builder().url(url(path)).build()));
    }

    void send(JSONObject context, String content, String screenshot, Runnable onSubmitted) throws Exception {
        JSONObject target = context.getJSONObject("target");
        JSONObject body = new JSONObject(context.optJSONObject("send_options").toString());
        JSONObject saved = new JSONObject(prefs.getString(optionsKey(target.optString("type"), target.optString("id")), "{}"));
        for (Iterator<String> it = saved.keys(); it.hasNext();) {
            String key = it.next();
            body.put(key, saved.get(key));
        }
        body.put("content", content);
        body.put("attachments", screenshot == null ? new org.json.JSONArray()
                : new org.json.JSONArray().put(screenshot));
        String generationId = UUID.randomUUID().toString();
        Request request = new Request.Builder().url(url(context.getString("send_url")))
                .header("X-Generation-Id", generationId).header("X-Generation-Target", target.getString("id"))
                .post(RequestBody.create(body.toString(), MediaType.get("application/json; charset=utf-8"))).build();
        // Keep the SSE connection open until generation completes. Closing it early can cancel a reply.
        Call call = client.newBuilder().readTimeout(0, TimeUnit.SECONDS).build().newCall(request);
        synchronized (this) {
            if (closed) throw new IOException("悬浮聊天已关闭");
            sending = call;
        }
        try (Response response = call.execute()) {
            consumeReply(response, onSubmitted);
        } finally { sending = null; }
    }

    static void consumeReply(Response response, Runnable onSubmitted) throws IOException {
        if (!response.isSuccessful() || response.body() == null) throw new IOException("发送失败（" + response.code() + "）");
        if (!response.header("Content-Type", "").contains("text/event-stream")) throw new IOException("发送未完成，请回到小家查看");
        // Submission is complete; generation may keep the SSE stream open for much longer.
        onSubmitted.run();
        String failure = null;
        while (!response.body().source().exhausted()) {
            String error = FloatingChatSession.failureFromSseLine(response.body().source().readUtf8Line());
            if (error != null) failure = error;
        }
        if (failure != null) throw new IOException(failure);
    }

    static String optionsKey(String type, String id) { return "floating_options_" + type + ":" + id; }

    synchronized void close() {
        closed = true;
        Call call = sending;
        if (call != null) call.cancel();
        client.dispatcher().cancelAll();
    }
}
