package com.aion.chat.homecoming;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

/** 2026-10-29 归巢全员：狗蛋和以后在座位 3～6 接的 AI 也能进归巢。 */
public class HomecomingSeatOwnersTest {
    private static final String WITH_SEATS =
            "{\"user\":{\"name\":\"小猫\",\"persona\":\"UP\"},\"companions\":{"
                    + "\"main\":{\"name\":\"小克\",\"persona\":\"MP\"},"
                    + "\"second\":{\"name\":\"Lucien\",\"persona\":\"SP\"},"
                    + "\"ai3\":{\"name\":\"狗蛋\",\"persona\":\"狗蛋的人设\"},"
                    + "\"ai5\":{\"name\":\"Kimi\",\"persona\":\"KP\"}},"
                    + "\"roster\":["
                    + "{\"owner\":\"main\",\"timeline\":\"main_private\",\"name\":\"小克\"},"
                    + "{\"owner\":\"second\",\"timeline\":\"companion_private\",\"name\":\"Lucien\"},"
                    + "{\"owner\":\"ai3\",\"timeline\":\"seat_private:ai3\",\"name\":\"狗蛋\","
                    + "\"model_key\":\"gemini-3.8-flash\"},"
                    + "{\"owner\":\"ai5\",\"timeline\":\"seat_private:ai5\",\"name\":\"Kimi\"},"
                    + "{\"owner\":\"ai6\",\"timeline\":\"seat_private:ai6\",\"name\":\"没人设\"},"
                    + "{\"owner\":\"ai9\",\"timeline\":\"seat_private:ai9\",\"name\":\"乱来\"}],"
                    + "\"group_members\":[\"main\",\"second\",\"ai5\",\"ai3\",\"ai6\",\"ai9\"],"
                    + "\"reply_order\":\"aion\"}";
    private static final String OLD_SNAPSHOT =
            "{\"user\":{\"name\":\"U\",\"persona\":\"\"},\"companions\":{"
                    + "\"main\":{\"name\":\"M\",\"persona\":\"\"},"
                    + "\"second\":{\"name\":\"S\",\"persona\":\"\"}},\"reply_order\":\"connor\"}";

    @Test
    public void ownersAndTimelinesMatchTheServer() {
        assertEquals("seat_private:ai3", HomecomingOwners.privateTimeline("ai3"));
        assertEquals("ai6", HomecomingOwners.ownerOfPrivateTimeline("seat_private:ai6"));
        assertEquals("second", HomecomingOwners.ownerOfPrivateTimeline("companion_private"));
        assertNull(HomecomingOwners.ownerOfPrivateTimeline("seat_private:ai9"));
        assertNull(HomecomingOwners.ownerOfPrivateTimeline("group"));
        assertTrue(HomecomingOwners.isTimeline("group"));
        assertFalse(HomecomingOwners.isTimeline("seat_private:"));
        assertFalse(HomecomingOwners.isOwner("ai9"));
        assertFalse(HomecomingOwners.hasLocalMemory("ai3"));
        assertThrows(IllegalArgumentException.class, () -> HomecomingOwners.privateTimeline("ai2"));
    }

    @Test
    public void rosterListsTheTwoCompanionsThenSeatsThatCameWithTheSnapshot() {
        List<HomecomingIdentityRepository.Member> roster =
                new HomecomingIdentityRepository(() -> WITH_SEATS).roster();
        List<String> owners = new ArrayList<>();
        for (HomecomingIdentityRepository.Member member : roster) owners.add(member.ownerId);
        assertEquals(Arrays.asList("main", "second", "ai3", "ai5"), owners);
        assertEquals("狗蛋", roster.get(2).name);
        assertEquals("seat_private:ai3", roster.get(2).timelineId);
        assertEquals("gemini-3.8-flash", roster.get(2).modelKey);
        assertEquals("", roster.get(3).modelKey);
    }

    @Test
    public void oldSnapshotsStillGiveJustTheTwoCompanions() {
        HomecomingIdentityRepository identities = new HomecomingIdentityRepository(() -> OLD_SNAPSHOT);
        assertEquals(2, identities.roster().size());
        assertEquals(Arrays.asList("second", "main"), identities.groupReplyOrder("turn"));
    }

    @Test
    public void groupOrderPutsReadySeatsAfterTheTwoCompanions() {
        assertEquals(Arrays.asList("main", "second", "ai5", "ai3"),
                new HomecomingIdentityRepository(() -> WITH_SEATS).groupReplyOrder("turn"));
    }

    @Test
    public void seatChatsRecallOnlyTheirOwnHubPack() {
        RecordingRecall recall = new RecordingRecall();
        HomecomingContextBuilder builder = new HomecomingContextBuilder(
                new HomecomingIdentityRepository(() -> WITH_SEATS), recall,
                (timeline, before, limit) -> Collections.emptyList(), 4000);

        String system = builder.build("seat_private:ai3", "main", "在吗", 10L, "").get(0).text;
        assertTrue(system.contains("当前对话角色：狗蛋"));
        assertTrue(system.contains("狗蛋的人设"));
        assertTrue(system.contains("ai3 的 Hub 记忆"));
        assertFalse(system.contains("main 的"));  // 不串到小克、Lucien 的包
        String group = builder.build("group", "ai5", "大家好", 10L, "").get(0).text;
        assertTrue(group.contains("当前对话角色：Kimi"));
        assertFalse(group.contains("ai3 的"));
        builder.build("group", "main", "q", 10L, "");
        assertEquals(Arrays.asList("ai3", "ai5", "main"), recall.owners);
    }

    @Test
    public void unknownSeatsAreRefused() {
        HomecomingIdentityRepository identities = new HomecomingIdentityRepository(() -> WITH_SEATS);
        assertThrows(IllegalArgumentException.class,
                () -> identities.identityForTimeline("seat_private:ai9", "ai9"));
        assertThrows(IllegalArgumentException.class,
                () -> identities.identityForTimeline("group", "ai9"));
        // 名单里有、快照里没带人设的座位：拿不到身份
        assertThrows(IllegalStateException.class,
                () -> identities.identityForTimeline("seat_private:ai6", "ai6"));
    }

    @Test
    public void groupTurnsRunEveryMemberInOrder() {
        List<String> starts = new ArrayList<>();
        List<String> done = new ArrayList<>();
        final boolean[] complete = {false};
        new HomecomingGroupTurnCoordinator(Arrays.asList("main", "second", "ai3"),
                new HomecomingGroupTurnCoordinator.ReplyPort() {
                    @Override public void start(String parent, String child, String owner,
                            boolean commitUser,
                            HomecomingGroupTurnCoordinator.ReplyCompletion completion) {
                        starts.add(child + ":" + commitUser);
                        completion.onComplete(owner + "-m", owner + "-t");
                    }
                    @Override public void cancel(String parent) {
                    }
                }).start("t", new HomecomingGroupTurnCoordinator.Observer() {
                    @Override public void onReplyComplete(String owner, String id, String text) {
                        done.add(owner);
                    }
                    @Override public void onReplyFailure(String owner, String code) {
                    }
                    @Override public void onTurnComplete() {
                        complete[0] = true;
                    }
                });
        assertEquals(Arrays.asList("t:true", "t:second:false", "t:ai3:false"), starts);
        assertEquals(Arrays.asList("main", "second", "ai3"), done);
        assertTrue(complete[0]);
        assertThrows(IllegalArgumentException.class, () ->
                new HomecomingGroupTurnCoordinator(Collections.emptyList(), null));
    }

    @Test
    public void avatarsComeOnlyFromSnapshotImageData() {
        String json = "{\"user\":{\"name\":\"U\",\"avatar\":\"data:image/png;base64,QUJD\"},"
                + "\"companions\":{\"main\":{\"name\":\"M\",\"avatar\":\"data:image/jpeg;base64,QUJD\"},"
                + "\"second\":{\"name\":\"S\",\"avatar\":\"https://example.invalid/a.png\"},"
                + "\"ai3\":{\"name\":\"狗蛋\",\"avatar\":\"data:image/svg+xml;base64,QUJD\"},"
                + "\"ai5\":{\"name\":\"Kimi\",\"avatar\":\"data:image/png;base64,QUJD\\\");x\"}},"
                + "\"roster\":[{\"owner\":\"ai3\"},{\"owner\":\"ai5\"}]}";
        HomecomingIdentityRepository identities = new HomecomingIdentityRepository(() -> json);
        List<HomecomingIdentityRepository.Member> roster = identities.roster();

        assertEquals("data:image/jpeg;base64,QUJD", roster.get(0).avatar);
        assertEquals("", roster.get(1).avatar);  // 外部链接不用
        assertEquals("", roster.get(2).avatar);  // svg 不收
        assertEquals("", roster.get(3).avatar);  // 夹带引号的不收
        assertEquals("data:image/png;base64,QUJD", identities.userAvatar());
        assertEquals("", new HomecomingIdentityRepository(() -> WITH_SEATS).userAvatar());
    }

    @Test
    public void conversationListIsBuiltFromTheRoster() throws Exception {
        String script = asset("homecoming.js");
        assertTrue(script.contains("function renderPeopleTabs()"));
        assertTrue(script.contains("identity.roster"));
        assertTrue(script.contains("closest(\"[data-timeline]\")"));  // 动态按钮也能点
        assertFalse(script.contains("identity.secondName || \"AI\""));
        assertTrue(script.contains("el(\"button\", \"person-tab group\", \"群聊\")"));
    }

    private static String asset(String name) throws Exception {
        Path path = Paths.get("src/main/assets/homecoming", name);
        if (!Files.exists(path)) path = Paths.get("app/src/main/assets/homecoming", name);
        return new String(Files.readAllBytes(path), StandardCharsets.UTF_8);
    }

    private static final class RecordingRecall implements HomecomingContextBuilder.MemoryRecall {
        final List<String> owners = new ArrayList<>();
        @Override
        public List<HomecomingMemoryRepository.Memory> recall(String owner, String query, int limit) {
            owners.add(owner);
            return Collections.singletonList(new HomecomingMemoryRepository.Memory(
                    "hub:" + owner + ":m1", owner, owner + " 的 Hub 记忆", "", "", false, 1L));
        }
    }
}
