package com.aion.chat;

import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;

/** Recent AI bodies for one conversation, merged with incoming messages on the UI thread. */
final class FloatingChatHistory {
    private final LinkedHashMap<String, JSONObject> messages = new LinkedHashMap<>();
    private String target = "";
    private int latestOffset;

    static String targetKey(JSONObject context) {
        JSONObject window = context.optJSONObject("target");
        return window == null ? "" : window.optString("type") + ":" + window.optString("id");
    }

    void reset(JSONObject context) {
        String key = targetKey(context);
        if (!key.equals(target)) { target = key; messages.clear(); latestOffset = 0; }
    }

    void merge(JSONObject context, JSONArray incoming) {
        reset(context);
        String type = "private".equals(context.optJSONObject("target").optString("type"))
                ? "msg_created" : "chatroom_msg_created";
        for (int i = 0; i < incoming.length(); i++) {
            JSONObject message = incoming.optJSONObject(i);
            if (FloatingChatSession.isVisibleMessage(context, type, message)) messages.put(message.optString("id"), message);
        }
        ArrayList<JSONObject> ordered = new ArrayList<>(messages.values());
        ordered.sort(Comparator.comparingDouble(message -> message.optDouble("created_at", 0)));
        messages.clear();
        for (int i = Math.max(0, ordered.size() - 100); i < ordered.size(); i++) {
            JSONObject message = ordered.get(i);
            messages.put(message.optString("id"), message);
        }
    }

    String latestBody() {
        String body = "";
        for (JSONObject message : messages.values()) body = FloatingChatSession.displayBody(message.optString("content"));
        return body;
    }

    String text(JSONObject context) {
        reset(context);
        StringBuilder text = new StringBuilder();
        JSONObject actors = context.optJSONObject("actors");
        for (JSONObject message : messages.values()) {
            if (text.length() > 0) text.append("\n\n");
            latestOffset = text.length();
            String actor = "private".equals(context.optJSONObject("target").optString("type"))
                    ? message.optString("role") : message.optString("sender");
            JSONObject info = actors == null ? null : actors.optJSONObject(actor);
            text.append(info == null ? "AI" : info.optString("name", "AI")).append('\n');
            text.append(FloatingChatSession.displayBody(message.optString("content")));
        }
        return text.toString();
    }

    int latestOffset() { return latestOffset; }
}
