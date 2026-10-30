package com.aion.chat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

/**
 * 通知标题用小猫给每位 AI 起的名字（2026-10-30：通知上显示的是 Aion / Connor / Ai3 这些内部代号）。
 * 名单来自服务器 /api/actors；拿不到时退回原来的写法（代号首字母大写）。
 */
final class ActorNames {
    private volatile Map<String, String> names = Collections.emptyMap();
    private volatile long loadedAt;

    static Map<String, String> parse(String body) {
        HashMap<String, String> result = new HashMap<>();
        try {
            JSONArray actors = new JSONObject(body).optJSONArray("actors");
            for (int index = 0; actors != null && index < actors.length(); index++) {
                JSONObject actor = actors.optJSONObject(index);
                if (actor == null) continue;
                String id = actor.optString("id", "").trim();
                String name = actor.optString("name", "").trim();
                if (!id.isEmpty() && !name.isEmpty()) result.put(id, name);
            }
        } catch (Exception ignored) {
            // 名单坏了就当没有，标题退回代号
        }
        return result;
    }

    static String label(String sender, Map<String, String> names) {
        if (sender == null || sender.isEmpty()) return "AI";
        String name = names.get(sender);
        if (name != null && !name.isEmpty()) return name;
        return sender.substring(0, 1).toUpperCase() + sender.substring(1);
    }

    String label(String sender) {
        return label(sender, names);
    }

    boolean isStale(long now, long maxAgeMillis) {
        return names.isEmpty() || now - loadedAt > maxAgeMillis;
    }

    void update(String body, long now) {
        Map<String, String> parsed = parse(body);
        if (parsed.isEmpty()) return;
        names = Collections.unmodifiableMap(parsed);
        loadedAt = now;
    }
}
