package com.aion.chat;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class FloatingChatHistoryTest {
    private JSONObject context(String id) throws Exception {
        return new JSONObject().put("target", new JSONObject().put("type", "chatroom").put("id", id))
                .put("actors", new JSONObject().put("aion", new JSONObject().put("name", "甲"))
                        .put("connor", new JSONObject().put("name", "乙")));
    }
    private JSONObject message(String id, String sender, String body, int time) throws Exception {
        return new JSONObject().put("id", id).put("room_id", "room").put("sender", sender)
                .put("content", body).put("created_at", time);
    }
    @Test public void historyMergesOldAndNewAiMessagesWithoutUsersThoughtsOrDuplicates() throws Exception {
        FloatingChatHistory history = new FloatingChatHistory();
        JSONObject context = context("room");
        history.merge(context, new JSONArray().put(message("new", "connor", "最新正文", 3)));
        history.merge(context, new JSONArray().put(message("user", "user", "不要显示", 1))
                .put(message("old", "aion", "【心里嘀咕：隐藏】之前正文", 2))
                .put(message("new", "connor", "最新正文", 3)));
        assertEquals("甲\n之前正文\n\n乙\n最新正文", history.text(context));
        assertEquals("最新正文", history.latestBody());
        assertEquals("乙\n最新正文", history.text(context).substring(history.latestOffset()));
    }
    @Test public void changingWindowDoesNotMixItsHistoryWithThePreviousWindow() throws Exception {
        FloatingChatHistory history = new FloatingChatHistory();
        history.merge(context("room"), new JSONArray().put(message("old", "aion", "旧窗口", 1)));
        history.merge(context("other"), new JSONArray().put(message("wrong", "connor", "旧窗口消息", 2)));
        assertEquals("", history.text(context("other")));
    }
}
