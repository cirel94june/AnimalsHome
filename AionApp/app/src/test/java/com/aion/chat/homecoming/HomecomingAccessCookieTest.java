package com.aion.chat.homecoming;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/**
 * 2026-10-30：10-24 服务器换到 Cloudflare Access 后面，归巢下载 / 回传没带登录状态，
 * 一直拿不到快照（手机上全是 0）。连自家服务器的要带；直连模型商、语音的绝不能带。
 */
public class HomecomingAccessCookieTest {
    @Test
    public void serverClientsCarryTheLoginCookie() throws Exception {
        assertTrue(source("HomecomingBackupClient.java").contains(".cookieJar(new com.aion.chat.WebViewCookieJar())"));
        assertTrue(source("HomecomingReturnClient.java").contains(".cookieJar(new com.aion.chat.WebViewCookieJar())"));
    }

    @Test
    public void thirdPartyClientsNeverSeeTheLoginCookie() throws Exception {
        assertFalse(source("HomecomingModelGateway.java").contains("cookieJar"));
        assertFalse(source("HomecomingTtsNetworkSynthesizer.java").contains("cookieJar"));
    }

    private static String source(String name) throws Exception {
        Path path = Paths.get("src/main/java/com/aion/chat/homecoming", name);
        if (!Files.exists(path)) path = Paths.get("app/src/main/java/com/aion/chat/homecoming", name);
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
    }
}
