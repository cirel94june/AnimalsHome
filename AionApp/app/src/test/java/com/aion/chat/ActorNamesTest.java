package com.aion.chat;

import org.junit.Test;

import java.util.Collections;
import java.util.Map;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
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
    }

    @Test
    public void unknownOrMissingFallsBackToTheOldTitle() {
        Map<String, String> names = ActorNames.parse(BODY);
        assertEquals("Ai4", ActorNames.label("ai4", names));
        assertEquals("AI", ActorNames.label("", names));
        assertEquals("Connor", ActorNames.label("connor", Collections.emptyMap()));
        assertTrue(ActorNames.parse("<html>login</html>").isEmpty());
    }

    @Test
    public void badRosterNeverWipesGoodNames() {
        ActorNames cache = new ActorNames();
        assertTrue(cache.isStale(0, 1000));
        cache.update(BODY, 100);
        assertFalse(cache.isStale(500, 1000));
        cache.update("<html>login</html>", 2000);  // Access 登录页之类
        assertEquals("狗蛋", cache.label("ai3"));
        assertTrue(cache.isStale(2000, 1000));
    }
}
