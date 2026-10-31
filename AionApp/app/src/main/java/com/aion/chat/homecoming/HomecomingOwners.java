package com.aion.chat.homecoming;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;

/**
 * 归巢里有哪些 AI（2026-10-29 归巢全员）。
 *
 * <p>原来只有 main（主伴侣）和 second（第二伴侣）；现在座位 ai3～ai6（狗蛋和以后接的 AI）
 * 也能进，各自的私聊时间线叫 seat_private:ai3。和服务器 homecoming/contracts.py 保持一致。
 */
final class HomecomingOwners {
    static final String MAIN = "main";
    static final String SECOND = "second";
    static final String GROUP = "group";
    static final String SEAT_TIMELINE_PREFIX = "seat_private:";
    private static final Set<String> SEATS = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("ai3", "ai4", "ai5", "ai6")));

    private HomecomingOwners() {
    }

    static boolean isSeat(String owner) {
        return owner != null && SEATS.contains(owner);
    }

    static boolean isOwner(String owner) {
        return MAIN.equals(owner) || SECOND.equals(owner) || isSeat(owner);
    }

    /** 只有原来的两位能在手机上写本地记忆；座位只能想起快照里带来的 Hub 只读记忆。 */
    static boolean hasLocalMemory(String owner) {
        return MAIN.equals(owner) || SECOND.equals(owner);
    }

    static String privateTimeline(String owner) {
        if (MAIN.equals(owner)) return "main_private";
        if (SECOND.equals(owner)) return "companion_private";
        if (isSeat(owner)) return SEAT_TIMELINE_PREFIX + owner;
        throw new IllegalArgumentException("unsupported owner");
    }

    /** 私聊时间线属于谁；群聊或不认识的返回 null。 */
    static String ownerOfPrivateTimeline(String timeline) {
        if ("main_private".equals(timeline)) return MAIN;
        if ("companion_private".equals(timeline)) return SECOND;
        if (timeline != null && timeline.startsWith(SEAT_TIMELINE_PREFIX)) {
            String seat = timeline.substring(SEAT_TIMELINE_PREFIX.length());
            return isSeat(seat) ? seat : null;
        }
        return null;
    }

    static boolean isTimeline(String timeline) {
        return GROUP.equals(timeline) || ownerOfPrivateTimeline(timeline) != null;
    }
}
