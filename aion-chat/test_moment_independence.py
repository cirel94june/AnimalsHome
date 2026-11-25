import asyncio
from unittest.mock import AsyncMock
import autonomy
import capabilities

def test_chat_moment_not_reserved_for_major_event_or_diary():
    text=capabilities.moment_ability_text()
    for piece in ("大声宣布","不必等重大事件","自己决定","没想分享就不发","不公开私聊","每轮最多一条"):
        assert piece in text

def test_optional_moment_can_accompany_rest_without_required_post():
    choice=autonomy._parse_action_choice({"action":"rest","reason":"困了","moment_content":"今天学会一件小事"},["rest","post_moment"])
    assert choice["moment_content"]=="今天学会一件小事"
    assert "moment_content" not in autonomy._parse_action_choice({"action":"rest"},["rest","post_moment"])
    assert "moment_content" not in autonomy._parse_action_choice({"action":"rest","moment_content":"x"},["rest"])

def test_actual_rest_can_publish_without_diary_or_extra_model_call(monkeypatch):
    import aiosqlite
    from contextlib import asynccontextmanager
    async def run():
        async with aiosqlite.connect(":memory:") as db:
            await db.execute("create table moments(author text,created_at real)")
            @asynccontextmanager
            async def connect():
                yield db
            monkeypatch.setattr(autonomy,"get_db",connect)
            monkeypatch.setattr(autonomy,"get_actor_config",AsyncMock(return_value={"actions":{"post_moment":True}}))
            select=AsyncMock(return_value={"action":"rest","reason":"想歇会儿","moment_content":"我今天有个好主意"})
            post=AsyncMock(return_value={"moment":{"id":"m","content":"我今天有个好主意"}})
            monkeypatch.setattr(autonomy,"_select_action",select)
            monkeypatch.setattr(autonomy,"_run_post_moment",post)
            monkeypatch.setattr(autonomy,"append_idle_event",AsyncMock(return_value={"id":"e"}))
            result=await autonomy._run_actor_once("connor")
            select.assert_awaited_once()
            post.assert_awaited_once_with("connor",{"message":"我今天有个好主意"})
            assert result["action"]=="rest"
    asyncio.run(run())
