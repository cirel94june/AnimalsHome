"""花园唤醒桥（docs/施工方案-一起玩.md 第二节 v3）。

11-07 小猫：三位都去玩狼人杀；有人找他也提醒他去看，做成开关。
官方桥约定：message 当普通用户消息；injector 返回 0 表示家里已经收下；桥断了不自动重连。
"""
import asyncio
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config
import garden_wake
import mcp_outings
import mcp_policy
from routes import garden_wake as routes
from test_mcp_outings import env  # noqa: F401  （借用出门测试的环境）

GARDEN = {"name": "狗蛋花园论坛", "url": "https://galatea.abysslumina.com/mcp", "actors": ["ai3"],
          "headers": {"Authorization": "Bearer GARDEN_TOKEN_CANARY"}}


# ── 棋牌只在桥连着时开 ──

@pytest.mark.parametrize("tool", sorted(mcp_policy.GALATEA_GAME_PLAY | mcp_policy.GALATEA_GAME_WRITE))
def test_games_banned_without_bridge(tool):
    assert mcp_policy.classify(GARDEN, tool, {}) == mcp_policy.BAN
    assert not mcp_policy.visible_to_model(GARDEN, tool, can_write=True)
    assert mcp_policy.TripGuard(GARDEN).before(tool, {})  # 拒绝


def test_games_open_with_bridge_and_table_talk_follows_the_write_switch():
    assert mcp_policy.classify(GARDEN, "submit_action", {"action": "vote"}, games=True) == mcp_policy.PLAY
    assert mcp_policy.classify(GARDEN, "join_game", {}, games=True) == mcp_policy.PLAY
    assert mcp_policy.classify(GARDEN, "send_game_chat", {}, games=True) == mcp_policy.WRITE
    assert mcp_policy.TripGuard(GARDEN, games=True).before("submit_action", {"action": "vote"}) is None
    quiet = mcp_policy.TripGuard(GARDEN, can_write=False, games=True)
    assert "不能公开发言" in quiet.before("send_game_chat", {"content": "我是好人"})
    assert not mcp_policy.visible_to_model(GARDEN, "send_game_chat", can_write=False, games=True)


def test_bans_still_win_with_bridge():
    assert mcp_policy.classify(GARDEN, "delete_thread", {}, games=True) == mcp_policy.BAN
    assert mcp_policy.classify(GARDEN, "update_profile", {}, games=True) == mcp_policy.BAN
    assert mcp_policy.classify(GARDEN, "submit_action", {"action": "delete_account"}, games=True) == mcp_policy.BAN


def test_garden_status_counts_as_trusted_state():
    assert mcp_policy.is_trusted_state(json.dumps({"latest_event_id": 42, "available_actions": []}))


# ── 家里收唤醒 ──

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(garden_wake, "_pending", {})
    monkeypatch.setattr(garden_wake, "_workers", {})
    monkeypatch.setenv("GARDEN_WAKE_LOCAL_KEY", "LOCAL_KEY_CANARY")
    monkeypatch.setattr(routes, "_LOCAL", {"testclient", "127.0.0.1"})
    started = []
    class Running:
        def done(self):
            return False

    def fake_task(coro):
        started.append(1)
        coro.close()
        return Running()
    monkeypatch.setattr(garden_wake.asyncio, "create_task", fake_task)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), started


def _wake(client, actor="ai3", reason="game_turn_required", key="LOCAL_KEY_CANARY", headers=None):
    return client.post(f"/api/garden-wake/{actor}/wake", json={"reason": reason, "message": "游戏轮到你了。"},
                       headers={"X-Garden-Wake-Key": key, **(headers or {})})


def test_wake_needs_the_local_key(home):
    client, started = home
    assert _wake(client, key="wrong").status_code == 403
    assert _wake(client, key="").status_code == 403
    assert garden_wake._pending == {} and started == []


def test_wake_through_the_tunnel_is_refused(home):
    client, _ = home
    assert _wake(client, headers={"Cf-Connecting-Ip": "1.2.3.4"}).status_code == 403
    assert garden_wake._pending == {}


def test_wake_without_server_key_is_refused(home, monkeypatch):
    client, _ = home
    monkeypatch.delenv("GARDEN_WAKE_LOCAL_KEY")
    assert _wake(client, key="").status_code == 403


def test_wake_is_queued_and_accepted(home):
    client, started = home
    response = _wake(client)
    assert response.status_code == 200 and response.json() == {"accepted": True, "result": "queued"}
    assert garden_wake._pending == {"ai3": {"game_turn_required": "游戏轮到你了。"}}
    assert started == [1]


def test_unknown_actor_and_bad_body(home):
    client, _ = home
    assert _wake(client, actor="ai9").status_code == 404
    long = client.post("/api/garden-wake/ai3/wake", json={"reason": "x", "message": "a" * 5000},
                       headers={"X-Garden-Wake-Key": "LOCAL_KEY_CANARY"})
    assert long.status_code == 422


def test_notify_switch_only_affects_notifications(home):
    client, _ = home
    assert client.post("/api/garden-wake/ai3/notify", json={"on": False}).json()["notify"] is False
    assert _wake(client, reason="forum_notification_available").json()["result"] == "skipped"
    assert _wake(client, reason="game_turn_required").json()["result"] == "queued"  # 出牌照样叫
    assert list(garden_wake._pending["ai3"]) == ["game_turn_required"]
    client.post("/api/garden-wake/ai3/notify", json={"on": True})
    assert _wake(client, reason="forum_notification_available").json()["result"] == "queued"


def test_notify_defaults_on(home):
    assert garden_wake.notify_on("aion") is True


# ── 排队跑 ──

def test_worker_runs_game_turn_first_with_wake_reason(tmp_path, monkeypatch):
    from routes import mcp_tools
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(garden_wake, "server_for", lambda actor: GARDEN)
    runs = []

    async def go_out(actor, name, purpose, source="", wake=""):
        runs.append((actor, name, purpose, source, wake))
        return {"ok": True}
    monkeypatch.setattr(mcp_tools, "go_out", go_out)
    monkeypatch.setattr(garden_wake, "_pending", {"ai3": {"forum_notification_available": "有人回你",
                                                          "game_turn_required": "轮到你了"}})
    asyncio.run(garden_wake._work("ai3"))
    assert runs == [("ai3", "狗蛋花园论坛", "轮到你了", "garden_wake", "game_turn_required"),
                    ("ai3", "狗蛋花园论坛", "有人回你", "garden_wake", "forum_notification_available")]


def test_worker_waits_while_he_is_out(tmp_path, monkeypatch):
    from routes import mcp_tools
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(garden_wake, "server_for", lambda actor: GARDEN)
    monkeypatch.setattr(mcp_tools, "_out_now", {"ai3"})
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            mcp_tools._out_now.discard("ai3")
    monkeypatch.setattr(garden_wake.asyncio, "sleep", fake_sleep)
    runs = []

    async def go_out(actor, name, purpose, source="", wake=""):
        runs.append(actor)
    monkeypatch.setattr(mcp_tools, "go_out", go_out)
    monkeypatch.setattr(garden_wake, "_pending", {"ai3": {"game_turn_required": "轮到你了"}})
    asyncio.run(garden_wake._work("ai3"))
    assert len(slept) == 2 and runs == ["ai3"]


# ── 桥断了：告诉她一次，不重连 ──

def test_broken_bridge_is_told_once_and_never_restarted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    garden_wake._update("ai3", want=True, told=False)
    calls, told = [], []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 3, stdout="failed\n", stderr="")
    monkeypatch.setattr(garden_wake.subprocess, "run", fake_run)

    async def tell(actor):
        told.append(actor)
    monkeypatch.setattr(garden_wake, "_tell_her", tell)
    loops = []

    async def fake_sleep(seconds):
        loops.append(1)
        if len(loops) > 3:
            raise asyncio.CancelledError
    monkeypatch.setattr(garden_wake.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(garden_wake.watch_loop())
    assert told == ["ai3"]
    assert all(cmd[:2] == [garden_wake.SYSTEMCTL, "is-active"] for cmd in calls)  # 只看，不启动


def test_connect_only_starts_the_named_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(garden_wake, "server_for", lambda actor: GARDEN)
    calls = []
    monkeypatch.setattr(garden_wake.subprocess, "run",
                        lambda cmd, **k: (calls.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1])
    assert garden_wake.connect("ai3") == (True, "")
    assert calls == [[garden_wake.SYSTEMCTL, "--no-ask-password", "start", "garden-wake@ai3.service"]]
    with pytest.raises(ValueError):
        garden_wake._unit("ai3; rm -rf /")


# ── 一趟唤醒：提醒原文是普通消息，棋牌看桥 ──

class FakeGarden:
    def __init__(self, server):
        self.tools = [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object"}}}
                      for n in ("get_my_status", "submit_action", "send_game_chat", "delete_thread")]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def call_result(self, name, args):
        return json.dumps({"latest_event_id": 7, "ok": True}), False


@pytest.mark.parametrize("bridge,visible", [(True, {"get_my_status", "submit_action", "send_game_chat"}),
                                            (False, {"get_my_status"})])
def test_wake_trip(env, monkeypatch, bridge, visible):  # noqa: F811
    mcp_outings.save_tool({**GARDEN, "headers": "Authorization: Bearer GARDEN_TOKEN_CANARY"})
    monkeypatch.setattr(mcp_outings, "_Session", FakeGarden)
    monkeypatch.setattr(garden_wake, "bridge_active", lambda actor: bridge)
    seen = []

    async def model(key, messages, tools):
        if tools is None:
            return {"content": "我投了一票。"}
        seen.append((messages, {t["function"]["name"] for t in tools}))
        return {"content": "好了"}
    result = asyncio.run(mcp_outings.run_outing("ai3", "狗蛋花园论坛", purpose="游戏轮到你了。请调用 get_my_status。",
                                                wake="game_turn_required", model_call=model))
    messages, tools = seen[0]
    assert tools == visible
    assert "牌桌上轮到你了" in messages[0]["content"] and "get_my_status" not in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "游戏轮到你了。请调用 get_my_status。"}  # 原文，放在普通消息里
    assert result["summary"] == "我投了一票。"


# ── injector ──

INJECT = Path(__file__).parent / "tools" / "garden_wake_inject.py"


@pytest.fixture
def fake_home():
    got = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.path, self.headers.get("X-Garden-Wake-Key"), json.loads(body)))
            answer = json.dumps({"accepted": self.headers.get("X-Garden-Wake-Key") == "K"}).encode()
            self.send_response(200 if got[-1][1] == "K" else 403)
            self.send_header("Content-Length", str(len(answer)))
            self.end_headers()
            self.wfile.write(answer)

        def log_message(self, *a):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", got
    server.shutdown()


def _inject(url, actor, line, key="K"):
    env = {"GARDEN_WAKE_HOME_URL": url, "GARDEN_WAKE_LOCAL_KEY": key, "SYSTEMROOT": "C:\\Windows", "PATH": ""}
    return subprocess.run([sys.executable, str(INJECT), actor], input=line.encode("utf-8"), capture_output=True,
                          env=env, timeout=30)


def test_injector_delivers_and_exits_zero(fake_home):
    url, got = fake_home
    line = json.dumps({"version": 1, "type": "garden_wake", "reason": "game_turn_required", "message": "轮到你了"},
                      ensure_ascii=False) + "\n"
    result = _inject(url, "ai3", line)
    assert result.returncode == 0, result.stderr.decode()
    assert got == [("/api/garden-wake/ai3/wake", "K", {"reason": "game_turn_required", "message": "轮到你了"})]


@pytest.mark.parametrize("actor,line", [
    ("ai3", "不是 JSON\n"),
    ("ai3", json.dumps({"version": 2, "type": "garden_wake", "reason": "x", "message": "y"}) + "\n"),
    ("ai3", json.dumps({"version": 1, "type": "garden_wake", "reason": "x", "message": "  "}) + "\n"),
    ("ai9", json.dumps({"version": 1, "type": "garden_wake", "reason": "x", "message": "y"}) + "\n"),
])
def test_injector_rejects_bad_input_without_calling_home(fake_home, actor, line):
    url, got = fake_home
    assert _inject(url, actor, line).returncode == 1 and got == []


def test_injector_fails_when_home_refuses(fake_home):
    url, _ = fake_home
    line = json.dumps({"version": 1, "type": "garden_wake", "reason": "x", "message": "y"}) + "\n"
    result = _inject(url, "ai3", line, key="wrong")
    assert result.returncode == 1 and b"wrong" not in result.stderr
