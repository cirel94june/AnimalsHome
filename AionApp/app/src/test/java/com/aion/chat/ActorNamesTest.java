package com.aion.chat;

import org.junit.Test;

import java.util.Collections;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

/** 2026-10-30：通知标题显示小猫起的名字，不是 Aion / Connor / Ai3。 */
public class ActorNamesTest {
    private static final String BODY = "{\"user\":{\"name\":\"小猫\"},\"actors\":["
            + "{\"id\":\"aion\",\"name\":\"小克\"},{\"id\":\"connor\",\"name\":\"Lucien\"},"
            + "{\"id\":\"ai3\",\"name\":\"狗蛋\"},{\"id\":\"ai4\",\"name\":\"\"}]}";

    @Test
    public void namesComeFromTheServerRoster() {
        Map<String, String> names = ActorNames.parse(BODY);
        assertEquals("小克", ActorNames.label("aion", names));
        assertEquals("Lucien", ActorNames.label("connor", names));
        assertEquals("狗蛋", ActorNames.label("ai3", names));
        assertEquals("Ai4", ActorNames.label("ai4", names));  // 空座位没名字：退回代号
    }

    @Test
    public void unknownOrMissingFallsBackToTheOldTitle() {
        assertEquals("AI", ActorNames.label("", Collections.emptyMap()));
        assertEquals("Connor", ActorNames.label("connor", Collections.emptyMap()));
    }

    @Test
    public void anyBadEntryRejectsTheWholeRoster() {
        assertNull(ActorNames.parse("<html>login</html>"));
        assertNull(ActorNames.parse("{\"actors\":[]}"));
        assertNull(ActorNames.parse("{\"actors\":{}}"));
        assertNull(ActorNames.parse("{\"actors\":[{\"id\":\"aion\",\"name\":17}]}"));
        assertNull(ActorNames.parse("{\"actors\":[{\"id\":\"ai3\",\"name\":{}}]}"));
        assertNull(ActorNames.parse("{\"actors\":[{\"id\":7,\"name\":\"x\"}]}"));
        assertNull(ActorNames.parse("{\"actors\":[{\"id\":\"ai9\",\"name\":\"x\"}]}"));
        assertNull(ActorNames.parse("{\"actors\":[{\"id\":\"aion\",\"name\":\"小克\"},\"oops\"]}"));
        assertNull(ActorNames.parse(  // 好条目和坏条目混在一起
                "{\"actors\":[{\"id\":\"aion\",\"name\":\"小克\"},{\"id\":\"ai3\",\"name\":[1]}]}"));
        assertNull(ActorNames.parse(  // 重复 id
                "{\"actors\":[{\"id\":\"aion\",\"name\":\"小克\"},{\"id\":\"aion\",\"name\":\"别人\"}]}"));
    }

    @Test
    public void badRosterKeepsGoodNamesAndCoolsDown() {
        ActorNames cache = new ActorNames();
        assertTrue(cache.claimRefresh(0, 1000));
        cache.finishRefresh(BODY, 100, 60_000);
        assertFalse(cache.claimRefresh(500, 1000));  // 还新鲜
        assertTrue(cache.claimRefresh(2000, 1000));
        cache.finishRefresh("{\"actors\":[{\"id\":\"aion\",\"name\":17}]}", 2000, 60_000);
        assertEquals("小克", cache.label("aion"));
        assertEquals("狗蛋", cache.label("ai3"));
        assertFalse(cache.claimRefresh(3000, 1000));  // 冷却中，不再连着请求
        assertTrue(cache.claimRefresh(62_001, 1000));
        cache.finishRefresh(null, 62_001, 60_000);  // 请求失败也冷却
        assertFalse(cache.claimRefresh(63_000, 1000));
    }

    @Test
    public void onlyOneRefreshAtATime() {
        ActorNames cache = new ActorNames();
        assertTrue(cache.claimRefresh(0, 1000));
        for (int i = 0; i < 10; i++) assertFalse(cache.claimRefresh(0, 1000));
        cache.finishRefresh(BODY, 1, 60_000);
        assertFalse(cache.stillNeeded(2, 1000));
    }

    @Test
    public void pushServiceUsesABoundedClientOffTheDeviceContextThread() throws Exception {
        java.nio.file.Path path = java.nio.file.Paths.get("src/main/java/com/aion/chat/AionPushService.java");
        if (!java.nio.file.Files.exists(path)) path = java.nio.file.Paths.get("app/" + path);
        String source = new String(java.nio.file.Files.readAllBytes(path),
                java.nio.charset.StandardCharsets.UTF_8);
        int start = source.indexOf("private void refreshActorNames()");
        String refresh = source.substring(start, source.indexOf("private String getHttpBase()", start));
        assertTrue(refresh.contains("actorNamesExecutor.execute"));
        assertFalse(refresh.contains("deviceContextExecutor"));
        assertTrue(refresh.contains(".callTimeout("));
        assertTrue(refresh.contains("claimRefresh"));
        assertTrue(source.contains("actorNamesExecutor.shutdownNow()"));
    }
}
