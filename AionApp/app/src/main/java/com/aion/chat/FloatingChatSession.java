package com.aion.chat;

import org.json.JSONObject;
import java.util.LinkedHashSet;
import java.util.regex.Pattern;

/** Only AI messages from the current conversation may open a floating card. */
public final class FloatingChatSession {
    private final LinkedHashSet<String> seen = new LinkedHashSet<>();
    // Match the main chat's separate monologue/metadata/command rendering.
    private static final Pattern INNER_MONOLOGUE = Pattern.compile(
            "[\\[【](?:心里嘀咕|内心嘀咕)\\s*[：:]\\s*[^\\]】]*[\\]】]");
    private static final Pattern INTERNAL_METADATA = Pattern.compile(
            "<(meta|autonomy_state)\\b[^>]*>.*?</\\1\\s*>", Pattern.DOTALL | Pattern.CASE_INSENSITIVE);
    private static final Pattern INLINE_TOY_COMMAND = Pattern.compile(
            "\\[(?:SVAKOM|ANKNI)\\b[^\\]]*(?:\\]|$)", Pattern.CASE_INSENSITIVE);
    private static final Pattern WISH_MARKER = Pattern.compile("\u2063wish_fulfillment:[A-Za-z0-9_-]+\u2063");

    public static String displayBody(String raw) {
        String body = INTERNAL_METADATA.matcher(raw == null ? "" : raw).replaceAll("");
        body = INLINE_TOY_COMMAND.matcher(body).replaceAll("");
        body = WISH_MARKER.matcher(body).replaceAll("");
        return INNER_MONOLOGUE.matcher(body).replaceAll("").trim();
    }

    public static String failureFromSseLine(String line) {
        if (line == null || !line.startsWith("data:")) return null;
        try {
            JSONObject event = new JSONObject(line.substring(5).trim());
            String type = event.optString("type");
            if ("stopped".equals(type)) return "回复已停止";
            if (!"error".equals(type) && !"stream_error".equals(type)) return null;
            return event.optString("content", event.optString("error", "生成失败，请回到小家查看"));
        } catch (Exception ignored) { return null; }
    }

    public boolean offer(JSONObject context, String type, JSONObject message) {
        if (!isVisibleMessage(context, type, message)) return false;
        String id = message.optString("id");
        if (id.isEmpty() || !seen.add(type + ":" + id)) return false;
        if (seen.size() > 100) seen.remove(seen.iterator().next());
        return true;
    }

    static boolean isVisibleMessage(JSONObject context, String type, JSONObject message) {
        JSONObject target = context.optJSONObject("target");
        if (target == null || message == null) return false;
        String actor;
        if ("private".equals(target.optString("type"))) {
            if (!"msg_created".equals(type)
                    || !target.optString("id").equals(message.optString("conv_id"))) return false;
            actor = message.optString("role");
            if (!"assistant".equals(actor)) return false;
        } else {
            if (!"chatroom_msg_created".equals(type)
                    || !target.optString("id").equals(message.optString("room_id"))) return false;
            actor = message.optString("sender");
            if (!isAiSender(actor)) return false;
        }
        return !message.optString("id").isEmpty() && !displayBody(message.optString("content")).isEmpty();
    }

    /** 主 AI、第二 AI，以及座位 3～6（ai3～ai6）。 */
    static boolean isAiSender(String actor) {
        return "aion".equals(actor) || "connor".equals(actor) || (actor != null && actor.matches("ai[3-6]"));
    }
}
