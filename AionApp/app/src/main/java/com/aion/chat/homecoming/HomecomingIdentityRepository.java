package com.aion.chat.homecoming;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public final class HomecomingIdentityRepository {
    private final Source source;

    public HomecomingIdentityRepository(HomecomingDatabase database) {
        this(new SQLiteSource(database));
    }

    HomecomingIdentityRepository(Source source) {
        this.source = source;
    }

    public Identity identityForTimeline(String timelineId, String responderOwner) {
        String owner = HomecomingOwners.ownerOfPrivateTimeline(timelineId);
        if (owner == null) {
            if (!HomecomingOwners.GROUP.equals(timelineId)) {
                throw new IllegalArgumentException("unsupported timeline");
            }
            owner = validateOwner(responderOwner);
        }
        try {
            JSONObject root = new JSONObject(source.load());
            JSONObject user = root.getJSONObject("user");
            JSONObject companion = root.getJSONObject("companions").getJSONObject(owner);
            return new Identity(
                    required(user.optString("name", ""), "configured user name"),
                    user.optString("persona", ""),
                    owner,
                    required(companion.optString("name", ""), "configured companion name"),
                    companion.optString("persona", ""),
                    root.optString("system_prompt", ""));
        } catch (Exception exception) {
            throw new IllegalStateException("verified identity snapshot is invalid", exception);
        }
    }

    public List<String> groupReplyOrder(String turnId) {
        JSONObject root;
        try {
            root = new JSONObject(source.load());
        } catch (Exception exception) {
            return Arrays.asList("main", "second");
        }
        String configured = root.optString("reply_order", "random")
                .trim().toLowerCase();
        List<String> order;
        if ("connor".equals(configured) || "second".equals(configured)) {
            order = new ArrayList<>(Arrays.asList("second", "main"));
        } else if ("aion".equals(configured) || "main".equals(configured)) {
            order = new ArrayList<>(Arrays.asList("main", "second"));
        } else {
            order = turnId.hashCode() % 2 == 0
                    ? new ArrayList<>(Arrays.asList("main", "second"))
                    : new ArrayList<>(Arrays.asList("second", "main"));
        }
        // 群里的座位排在两位后面，按服务器给的顺序；旧快照没有 group_members，照旧两位
        JSONArray members = root.optJSONArray("group_members");
        JSONObject companions = root.optJSONObject("companions");
        if (members != null && companions != null) {
            for (int index = 0; index < members.length(); index++) {
                String owner = members.optString(index, "");
                if (HomecomingOwners.isSeat(owner) && companions.has(owner)
                        && !order.contains(owner)) {
                    order.add(owner);
                }
            }
        }
        return order;
    }

    /** 归巢里能聊的每位 AI：主伴侣、第二伴侣，以及快照里带来的座位。 */
    public List<Member> roster() {
        ArrayList<Member> result = new ArrayList<>();
        try {
            JSONObject root = new JSONObject(source.load());
            JSONObject companions = root.getJSONObject("companions");
            for (String owner : new String[]{"main", "second"}) {
                JSONObject companion = companions.optJSONObject(owner);
                result.add(new Member(owner, HomecomingOwners.privateTimeline(owner),
                        companion == null ? owner : companion.optString("name", owner), "",
                        companion == null ? "" : companion.optString("avatar", "")));
            }
            JSONArray listed = root.optJSONArray("roster");
            for (int index = 0; listed != null && index < listed.length(); index++) {
                JSONObject item = listed.optJSONObject(index);
                String owner = item == null ? "" : item.optString("owner", "");
                JSONObject companion = companions.optJSONObject(owner);
                if (!HomecomingOwners.isSeat(owner) || companion == null) continue;
                String name = companion.optString("name", "").trim();
                if (name.isEmpty()) continue;
                result.add(new Member(owner, HomecomingOwners.privateTimeline(owner),
                        name, item.optString("model_key", ""), companion.optString("avatar", "")));
            }
        } catch (Exception exception) {
            throw new IllegalStateException("verified identity snapshot is invalid", exception);
        }
        return result;
    }

    public static final class Member {
        public final String ownerId;
        public final String timelineId;
        public final String name;
        public final String modelKey;
        /** 服务器打包进来的头像（data:image/…），没有就是空串，页面上退回名字首字。 */
        public final String avatar;

        Member(String ownerId, String timelineId, String name, String modelKey) {
            this(ownerId, timelineId, name, modelKey, "");
        }

        Member(String ownerId, String timelineId, String name, String modelKey, String avatar) {
            this.ownerId = ownerId;
            this.timelineId = timelineId;
            this.name = name;
            this.modelKey = modelKey == null ? "" : modelKey;
            this.avatar = safeAvatar(avatar);
        }
    }

    private static final java.util.regex.Pattern AVATAR = java.util.regex.Pattern.compile(
            "data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*");
    static final int MAX_AVATAR_CHARS = 600_000;

    /** 只认快照里自带的图片数据；外部链接一律不用（断网也用不了，还会往外发请求）。 */
    static String safeAvatar(String value) {
        if (value == null || value.length() > MAX_AVATAR_CHARS) return "";
        return AVATAR.matcher(value).matches() ? value : "";
    }

    /** 小猫自己的头像。 */
    public String userAvatar() {
        try {
            JSONObject user = new JSONObject(source.load()).optJSONObject("user");
            return user == null ? "" : safeAvatar(user.optString("avatar", ""));
        } catch (Exception exception) {
            return "";
        }
    }

    interface Source {
        String load();
    }

    private static final class SQLiteSource implements Source {
        private final HomecomingDatabase helper;
        SQLiteSource(HomecomingDatabase helper) { this.helper = helper; }
        @Override public String load() {
            SQLiteDatabase database = helper.getReadableDatabase();
            try (Cursor cursor = database.rawQuery(
                    "SELECT payload_json FROM identity_snapshot WHERE identity_id='identity'",
                    null)) {
                if (!cursor.moveToFirst()) {
                    throw new IllegalStateException("identity snapshot is missing");
                }
                return cursor.getString(0);
            }
        }
    }

    public static final class Identity {
        public final String userName;
        public final String userPersona;
        public final String ownerId;
        public final String companionName;
        public final String companionPersona;
        public final String systemPrompt;

        Identity(String userName, String userPersona, String ownerId,
                String companionName, String companionPersona, String systemPrompt) {
            this.userName = userName;
            this.userPersona = userPersona;
            this.ownerId = ownerId;
            this.companionName = companionName;
            this.companionPersona = companionPersona;
            this.systemPrompt = systemPrompt;
        }
    }

    private static String validateOwner(String owner) {
        if (!HomecomingOwners.isOwner(owner)) {
            throw new IllegalArgumentException("unsupported memory owner");
        }
        return owner;
    }

    private static String required(String value, String label) {
        if (value == null || value.trim().isEmpty()) {
            throw new IllegalArgumentException(label + " is required");
        }
        return value.trim();
    }
}
