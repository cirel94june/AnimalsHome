package com.aion.chat;

import okhttp3.*;
import org.junit.Test;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;
import static org.junit.Assert.*;

public class FloatingChatSubmissionTest {
    private Response response(String contentType, String content) {
        return new Response.Builder().request(new Request.Builder().url("http://localhost/send").build())
                .protocol(Protocol.HTTP_1_1).code(200).message("OK")
                .header("Content-Type", contentType).body(ResponseBody.create(content, MediaType.get(contentType))).build();
    }
    @Test public void acceptedSendIsReportedBeforeAiGenerationFinishesOrFails() throws Exception {
        AtomicBoolean submitted = new AtomicBoolean();
        try (Response response = response("text/event-stream", "data: {\"type\":\"error\",\"content\":\"生成失败\"}\n")) {
            try {
                FloatingChatClient.consumeReply(response, () -> submitted.set(true));
                fail("Generation failure should still be visible");
            } catch (IOException expected) {
                assertEquals("生成失败", expected.getMessage());
                assertTrue("The user message was submitted before the generation error", submitted.get());
            }
        }
    }
    @Test public void invalidResponseDoesNotClearAnUnsubmittedDraft() throws Exception {
        AtomicBoolean submitted = new AtomicBoolean();
        try (Response response = response("application/json", "{}")) {
            try { FloatingChatClient.consumeReply(response, () -> submitted.set(true)); fail("Invalid response"); }
            catch (IOException expected) { assertFalse(submitted.get()); }
        }
    }
}
