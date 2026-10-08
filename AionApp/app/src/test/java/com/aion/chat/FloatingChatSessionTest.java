package com.aion.chat;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class FloatingChatSessionTest {
    private JSONObject context(String type, String id) throws Exception {
        return new JSONObject().put("target", new JSONObject().put("type", type).put("id", id));
    }

    @Test public void onlyLatestRoomMessagesAppearAndDuplicatesAreIgnored() throws Exception {
        FloatingChatSession session = new FloatingChatSession();
        JSONObject message = new JSONObject().put("id", "m1").put("room_id", "old").put("sender", "connor").put("content", "正文");
        assertFalse(session.offer(context("chatroom", "current"), "chatroom_msg_created", message));
        message.put("room_id", "current");
        assertTrue(session.offer(context("chatroom", "current"), "chatroom_msg_created", message));
        assertFalse(session.offer(context("chatroom", "current"), "chatroom_msg_created", message));
    }

    @Test public void onlyBothAiSendersAppearAndUserRepliesStayHidden() throws Exception {
        FloatingChatSession session = new FloatingChatSession();
        assertFalse(session.offer(context("chatroom", "group"), "chatroom_msg_created",
                new JSONObject().put("id", "user").put("room_id", "group").put("sender", "user")));
        for (String sender : new String[]{"aion", "connor"}) {
            assertTrue(session.offer(context("chatroom", "group"), "chatroom_msg_created",
                    new JSONObject().put("id", sender).put("room_id", "group").put("sender", sender).put("content", "正文")));
        }
    }

    @Test public void privateWindowDoesNotAcceptGroupMessagesOrSystemNotices() throws Exception {
        FloatingChatSession session = new FloatingChatSession();
        JSONObject message = new JSONObject().put("id", "m").put("conv_id", "private").put("role", "assistant").put("content", "正文");
        assertFalse(session.offer(context("private", "other"), "msg_created", message));
        assertTrue(session.offer(context("private", "private"), "msg_created", message));
        assertFalse(session.offer(context("private", "private"), "msg_created",
                new JSONObject().put("id", "user").put("conv_id", "private").put("role", "user")));
        assertFalse(session.offer(context("private", "private"), "chatroom_msg_created",
                new JSONObject().put("id", "system").put("room_id", "private").put("sender", "system")));
    }

    @Test public void thoughtsWithoutBodyDoNotOpenOrReplaceTheFloatingMessage() throws Exception {
        FloatingChatSession session = new FloatingChatSession();
        assertFalse(session.offer(context("chatroom", "group"), "chatroom_msg_created",
                new JSONObject().put("id", "thought").put("room_id", "group").put("sender", "connor")
                        .put("content", "【心里嘀咕：想抱抱她。】")));
        assertFalse(session.offer(context("private", "private"), "msg_created",
                new JSONObject().put("id", "meta").put("conv_id", "private").put("role", "assistant")
                        .put("content", "<meta>内部状态</meta>")));
    }

    @Test public void floatingBodyKeepsDialogueAroundThoughts() {
        assertEquals("宝宝，回来啦？\n按住按钮回复我。", FloatingChatSession.displayBody(
                "<meta>内部状态\n不显示</meta>【心里嘀咕：想抱抱她。】宝宝，回来啦？\n"
                        + "[内心嘀咕: 偷偷开心]按住按钮回复我。[心里嘀咕：等她回应。]"));
    }

    @Test public void floatingBodyHidesControlMarkersAndPreservesOrdinaryText() {
        assertEquals("看看【这个帖子】：[链接](https://example.com)", FloatingChatSession.displayBody(
                "看看【这个帖子】：[链接](https://example.com)[SVAKOM: 1]\n"
                        + "<autonomy_state>内部状态</autonomy_state>\u2063wish_fulfillment:abc\u2063"));
    }

    @Test public void streamingFailuresAreVisibleInsteadOfLookingLikeSuccess() {
        assertNull(FloatingChatSession.failureFromSseLine("data: {\"type\":\"chunk\",\"content\":\"hello\"}"));
        assertEquals("生成失败", FloatingChatSession.failureFromSseLine("data: {\"type\":\"error\",\"content\":\"生成失败\"}"));
        assertEquals("连接断开", FloatingChatSession.failureFromSseLine("data: {\"type\":\"stream_error\",\"content\":\"连接断开\"}"));
    }
}
