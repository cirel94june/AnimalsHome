"""§63 复审：座位自主活动固定身份和线路（M1）、小红书发出前再核对（M2）、同一位不重复巡游（M3）、点名按编号（L1）。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import autonomy
import seat_chat


@pytest.fixture
def seat(monkeypatch):
    import config
    import memory_hub_jobs
    state = {"ready": True, "model": "seat-m", "route": "seat-m"}
    monkeypatch.setattr(config, "MODELS", {**config.MODELS, "seat-m": {"provider": "custom_openai", "model": "ORIGINAL"},
                                           "other-m": {"provider": "custom_openai", "model": "OTHER"}})

    def check_ready(actor):
        if not state["ready"]:
            raise seat_chat.SeatNotReady("off")
        return {}, state["model"]
    monkeypatch.setattr(seat_chat, "check_ready", check_ready)
    monkeypatch.setattr(memory_hub_jobs, "background_model", lambda actor, model: state["route"])
    monkeypatch.setattr(memory_hub_jobs, "_persona_messages", lambda actor: [{"role": "user", "content": "人设"}])
    monkeypatch.setattr(autonomy, "load_worldbook", lambda: {})
    calls = []

    async def stream(messages, model_key, *a, **k):
        calls.append(model_key)
        yield '{"ok": true}'
    monkeypatch.setattr(autonomy, "stream_ai", stream)
    autonomy._SEAT_PINS.set(None)
    return state, calls, config


def _context_then(change):
    import routes.chatroom as chatroom_routes

    async def load(room_id, limit=30):
        change()
        return {}, []
    return load


@pytest.mark.parametrize("change", ["rebind", "disable", "reselect", "background"])
def test_seat_changes_during_context_stop_the_model_call(seat, monkeypatch, change):
    """M1：准备上下文的等待期间改绑线路、停用、改选模型、换后台线路，都不再调模型。"""
    state, calls, config = seat
    import routes.chatroom as chatroom_routes

    def mutate():
        if change == "rebind":
            config.MODELS["seat-m"] = {"provider": "custom_openai", "model": "OTHER_MODEL"}
        elif change == "disable":
            state["ready"] = False
        elif change == "reselect":
            state["model"] = "other-m"
        else:
            state["route"] = "other-m"
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", _context_then(mutate))

    async def go():
        autonomy.pin_seat("ai3")  # 活动入口
        return await autonomy._ask_actor_json("ai3", "做点什么")
    with pytest.raises(Exception):
        asyncio.run(go())
    assert calls == []


def test_unchanged_seat_still_calls_its_own_route(seat, monkeypatch):
    state, calls, config = seat
    import routes.chatroom as chatroom_routes
    monkeypatch.setattr(chatroom_routes, "_get_or_create_seat_room", AsyncMock(return_value="s3"))
    monkeypatch.setattr(chatroom_routes, "_load_room_and_messages", _context_then(lambda: None))

    async def go():
        autonomy.pin_seat("ai3")
        return await autonomy._ask_actor_json("ai3", "做点什么")
    assert asyncio.run(go()) == {"ok": True}
    assert calls == ["seat-m"]


def test_each_activity_starts_with_a_fresh_pin(seat):
    """入口重新固定：上一次活动之后改了模型，下一次活动按新的来，不会一直卡在旧的那份。"""
    state, calls, config = seat

    async def go():
        autonomy.pin_seat("ai3")
        state["model"] = state["route"] = "other-m"
        with pytest.raises(RuntimeError):
            autonomy.verify_seat_pin("ai3")
        autonomy.pin_seat("ai3")
        return autonomy.verify_seat_pin("ai3")["route"]
    assert asyncio.run(go()) == "other-m"


# ── 小红书 ──

def _patch_sleep(monkeypatch, xhs_lite, sleep):
    import types
    proxy = types.SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")})
    proxy.sleep = sleep
    monkeypatch.setattr(xhs_lite, "asyncio", proxy)


@pytest.fixture
def xhs(seat, monkeypatch, tmp_path):
    import xhs_lite
    monkeypatch.setattr(xhs_lite, "CONFIG_PATH", tmp_path / "xhs.json")
    xhs_lite.save_config({"enabled": True, "auto_enabled": True, "allow_write_comments": True, "cookie": "c",
                          "target_user_id": "u", "write_delay_seconds": 5, "actors": {"ai3": {"enabled": True}}})
    import actors
    monkeypatch.setattr(actors, "display_name", lambda a: "狗蛋")
    monkeypatch.setattr(xhs_lite, "_resolve_target", AsyncMock(return_value={"user_id": "u", "nickname": "她"}))
    monkeypatch.setattr(xhs_lite, "_extract_notes", lambda payload: [{"note_id": "n1", "title": "t"}])
    monkeypatch.setattr(xhs_lite, "_extract_note_content", lambda payload: {})
    monkeypatch.setattr(xhs_lite, "_extract_comments", lambda payload: [])
    posted = []

    async def worker(name, payload, **kw):
        if name in ("post-comment", "reply-comment"):
            posted.append(payload)
            return {"success": True}
        return {}
    monkeypatch.setattr(xhs_lite, "_run_worker", worker)
    monkeypatch.setattr(xhs_lite, "_ask_actor_decision", AsyncMock(return_value={"action": "comment", "comment": "好看"}))
    monkeypatch.setattr(xhs_lite, "append_log", lambda entry: None, raising=False)
    return xhs_lite, posted


@pytest.mark.parametrize("change", ["disable_seat", "actor_switch", "write_switch", "global_switch"])
def test_xhs_checks_again_right_before_posting(xhs, seat, monkeypatch, change):
    """M2：等待发出的那几秒里停用座位、关掉这一位 / 写评论 / 总开关，都不发，留成草稿。"""
    xhs_lite, posted = xhs
    state = seat[0]

    async def sleep(seconds):
        if change == "disable_seat":
            state["ready"] = False
        elif change == "actor_switch":
            xhs_lite.save_config({"actors": {"ai3": {"enabled": False}}})
        elif change == "write_switch":
            xhs_lite.save_config({"allow_write_comments": False})
        else:
            xhs_lite.save_config({"enabled": False})
    _patch_sleep(monkeypatch, xhs_lite, sleep)
    result = asyncio.run(xhs_lite.run_actor_roam("ai3", manual=True))
    assert posted == [] and result["status"] == "drafted" and result["wrote"] is False


def test_xhs_posts_when_nothing_changed(xhs, monkeypatch):
    xhs_lite, posted = xhs
    _patch_sleep(monkeypatch, xhs_lite, AsyncMock())
    result = asyncio.run(xhs_lite.run_actor_roam("ai3", manual=True))
    assert len(posted) == 1 and result["wrote"] is True


def test_xhs_same_actor_never_runs_twice_at_once(xhs, monkeypatch):
    """M3：同一位同时只跑一次（手动、自动都走 run_actor_roam），第二次直接说忙，不会发两条。"""
    xhs_lite, posted = xhs
    gate = asyncio.Event()

    async def sleep(seconds):
        await gate.wait()
    _patch_sleep(monkeypatch, xhs_lite, sleep)

    async def go():
        first = asyncio.create_task(xhs_lite.run_actor_roam("ai3", manual=True))
        await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="正在巡游"):
            await xhs_lite.run_actor_roam("ai3", manual=True)
        gate.set()
        return await first
    result = asyncio.run(go())
    assert result["wrote"] is True and len(posted) == 1


def test_partner_id_wins_over_a_display_name(monkeypatch):
    """L1：先认编号；名字只有一位叫这个时才认，重名不猜。"""
    monkeypatch.setattr(autonomy, "_family_partners", lambda actor: ["connor", "ai3", "ai4"])
    names = {"connor": "Lucien", "ai3": "ai4", "ai4": "小四"}  # ai3 的名字被改成了「ai4」
    monkeypatch.setattr(autonomy, "_actor_label", lambda a: names.get(a, a))
    assert autonomy._pick_partner("aion", "ai4") == "ai4"
    assert autonomy._pick_partner("aion", "小四") == "ai4"
    names["ai3"] = "小四"  # 两位都叫小四
    assert autonomy._pick_partner("aion", "小四") == "connor"  # 不猜，按默认
