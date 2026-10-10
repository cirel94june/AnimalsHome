package com.aion.chat;

import org.junit.Test;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

public class ConnectionEndpointTest {
    @Test
    public void normalizesTypedServerAddress() {
        assertEquals("https://box.tail1234.ts.net/",
                ConnectionEndpoint.normalizeServerInput("  box.tail1234.ts.net "));
        assertEquals("https://box.tail1234.ts.net/",
                ConnectionEndpoint.normalizeServerInput("HTTPS://box.tail1234.ts.net"));
        assertEquals("http://100.64.0.1:8080/chat",
                ConnectionEndpoint.normalizeServerInput("http://100.64.0.1:8080/chat"));
        assertEquals("wss://box.tail1234.ts.net/ws",
                ConnectionEndpoint.toWebSocketUrl(ConnectionEndpoint.normalizeServerInput("box.tail1234.ts.net")));
        assertEquals(null, ConnectionEndpoint.normalizeServerInput(""));
        assertEquals(null, ConnectionEndpoint.normalizeServerInput("ftp://box"));
        assertEquals(null, ConnectionEndpoint.normalizeServerInput("https://"));
        assertEquals(null, ConnectionEndpoint.normalizeServerInput(null));
    }

    @Test
    public void migratesLegacyCloudflareHost() {
        assertEquals(ConnectionEndpoint.CLOUDFLARE_PAGE_URL,
                ConnectionEndpoint.normalizePageUrl("https://legacy-ws.example.com/chat"));
        assertEquals("wss://chat.example.com/ws",
                ConnectionEndpoint.toWebSocketUrl("wss://legacy-ws.example.com/ws"));
    }

    @Test
    public void buildsCloudflareWebSocketOnProtectedHost() {
        assertEquals("wss://chat.example.com/ws",
                ConnectionEndpoint.toWebSocketUrl("https://chat.example.com/chat"));
        assertTrue(ConnectionEndpoint.isCloudflareHost("chat.example.com"));
    }

    @Test
    public void preservesTailscaleAndLanRoutes() {
        assertEquals("ws://100.64.0.1:8080/ws",
                ConnectionEndpoint.toWebSocketUrl("http://100.64.0.1:8080/chat"));
        assertEquals("ws://192.168.1.100:8080/ws",
                ConnectionEndpoint.toWebSocketUrl("http://192.168.1.100:8080/chat"));
        assertFalse(ConnectionEndpoint.isCloudflareHost("100.64.0.1"));
        assertFalse(ConnectionEndpoint.isCloudflareHost("chat.example.com.evil.example"));
    }

    @Test
    public void recognizesOnlyExactCloudflareAccessCookieName() {
        assertTrue(ConnectionEndpoint.hasCloudflareAccessCookie(
                "session=one; CF_Authorization=token; theme=dark"));
        assertFalse(ConnectionEndpoint.hasCloudflareAccessCookie(
                "NotCF_Authorization=token; session=one"));
        assertFalse(ConnectionEndpoint.hasCloudflareAccessCookie(null));
    }

    @Test
    public void recognizesOnlyCloudflareAccessDomainBoundaries() {
        assertTrue(ConnectionEndpoint.isCloudflareAccessHost("cloudflareaccess.com"));
        assertTrue(ConnectionEndpoint.isCloudflareAccessHost(
                "super-paper-137a.cloudflareaccess.com"));
        assertFalse(ConnectionEndpoint.isCloudflareAccessHost(
                "cloudflareaccess.com.evil.example"));
        assertFalse(ConnectionEndpoint.isCloudflareAccessHost(
                "evilcloudflareaccess.com"));
        assertFalse(ConnectionEndpoint.isCloudflareAccessHost(null));
    }

    @Test
    public void bypassesOnlyProtectedCloudflareMainDocument() {
        assertTrue(ConnectionEndpoint.shouldBypassCloudflareMainDocument(
                ConnectionEndpoint.CLOUDFLARE_PAGE_URL,
                ConnectionEndpoint.CLOUDFLARE_PAGE_URL, true));
        assertFalse(ConnectionEndpoint.shouldBypassCloudflareMainDocument(
                ConnectionEndpoint.CLOUDFLARE_PAGE_URL,
                "https://chat.example.com/static/chatroom.css", false));
        assertFalse(ConnectionEndpoint.shouldBypassCloudflareMainDocument(
                "http://100.64.0.1:8080/chat",
                "http://100.64.0.1:8080/chat", true));
        assertFalse(ConnectionEndpoint.shouldBypassCloudflareMainDocument(
                ConnectionEndpoint.CLOUDFLARE_PAGE_URL,
                "https://chat.example.com.evil.example/chat", true));
    }

    @Test
    public void allowsOnlyExactSelectedContentHost() {
        String tailscalePage = "http://100.64.0.1:8080/chat";
        assertTrue(ConnectionEndpoint.isAllowedContentHost("100.64.0.1", tailscalePage));
        assertTrue(ConnectionEndpoint.isAllowedContentHost("chat.example.com",
                ConnectionEndpoint.CLOUDFLARE_PAGE_URL));
        assertFalse(ConnectionEndpoint.isAllowedContentHost(
                "100.64.0.1.evil.example", tailscalePage));
        assertFalse(ConnectionEndpoint.isAllowedContentHost(
                "evil192.168.1.100.example", "http://192.168.1.100:8080/chat"));
    }
}
