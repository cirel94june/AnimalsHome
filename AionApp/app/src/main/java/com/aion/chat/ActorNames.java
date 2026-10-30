package com.aion.chat;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 通知标题用小猫给每位 AI 起的名字（2026-10-30：通知上显示的是 Aion / Connor / Ai3 这些内部代号）。
 * 名单来自服务器 /api/actors；拿不到时退回原来的写法（代号首字母大写）。
 */
final class ActorNames {
    private static final Set<String> KNOWN_IDS = Collections.unmodifiableSet(new HashSet<>(
            Arrays.asList("aion", "connor", "ai3", "ai4", "ai5", "ai6")));

    private volatile Map<String, String> names = Collections.emptyMap();
    private volatile long loadedAt;
    private volatile long retryAfter;
    private final AtomicBoolean refreshing = new AtomicBoolean(false);

    /** 整份校验通过才返回名单；任何一条类型不对、id 不认识或重复，就当这份名单坏了（复审 L1）。 */
    static Map<String, String> parse(String body) {
        try {
            Object actors = new JSONObject(body).opt("actors");
            if (!(actors instanceof JSONArray)) return null;
            JSONArray list = (JSONArray) actors;
            HashMap<String, String> result = new HashMap<>();
            HashSet<String> seen = new HashSet<>();
            for (int index = 0; index < list.length(); index++) {
                Object item = list.opt(index);
                if (!(item instanceof JSONObject)) return null;
                Object id = ((JSONObject) item).opt("id");
                Object name = ((JSONObject) item).opt("name");
                if (!(id instanceof String) || !KNOWN_IDS.contains(id) || !seen.add((String) id)) return null;
                if (name != null && name != JSONObject.NULL && !(name instanceof String)) return null;
                String text = name instanceof String ? ((String) name).trim() : "";
                if (!text.isEmpty()) result.put((String) id, text);  // 空座位没名字：跳过，不算坏
            }
            return result.isEmpty() ? null : result;
        } catch (Exception ignored) {
            return null;
        }
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

    /** 需要刷新、没有别的刷新在跑、也不在失败冷却里时，认领这次刷新；调用方必须 finishRefresh。 */
    boolean claimRefresh(long now, long maxAgeMillis) {
        boolean needed = names.isEmpty() || now - loadedAt > maxAgeMillis;
        if (!needed || now < retryAfter) return false;
        return refreshing.compareAndSet(false, true);
    }

    boolean stillNeeded(long now, long maxAgeMillis) {
        return names.isEmpty() || now - loadedAt > maxAgeMillis;
    }

    /** body 为 null 表示请求失败。好名单才发布；坏的或失败都保留旧名单，并冷却一会儿再试。 */
    void finishRefresh(String body, long now, long cooldownMillis) {
        try {
            Map<String, String> parsed = body == null ? null : parse(body);
            if (parsed != null) {
                names = Collections.unmodifiableMap(parsed);
                loadedAt = now;
            } else {
                retryAfter = now + cooldownMillis;
            }
        } finally {
            refreshing.set(false);
        }
    }
}
