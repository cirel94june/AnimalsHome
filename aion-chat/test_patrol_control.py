"""2026-10-28 小猫：每位 AI 都能自己停 / 开哨兵巡逻，停多久自己定（写时长到点恢复，不写就一直停）。"""

import asyncio
import time
from pathlib import Path

import pytest

import camera
import patrol_control

HERE = Path(__file__).parent


@pytest.fixture
def cam(monkeypatch):
    monitor = camera.CameraMonitor()
    monitor._monitor_loop = lambda gen=None: None  # 不真的去截图
    monkeypatch.setattr(camera, "cam", monitor)
    monkeypatch.setattr(camera, "save_cam_config", lambda cfg: None)
    monkeypatch.setattr(patrol_control, "_name", lambda actor: {"aion": "小克", "ai3": "狗蛋"}.get(actor, actor))
    return monitor


def test_reads_the_ways_an_ai_might_write_it():
    text, cmds = patrol_control.extract("好，我先歇会儿[巡逻:停|3小时]晚安")
    assert text == "好，我先歇会儿晚安" and cmds == [("stop", "3小时")]
    assert patrol_control.extract("【巡逻：暂停｜90分钟】")[1] == [("stop", "90分钟")]
    assert patrol_control.extract("[巡逻:开]")[1] == [("start", "")]
    assert patrol_control.extract("[巡逻:跳舞]") == ("", [])  # 看不懂：去掉，不执行
    assert patrol_control.parse_duration("3小时") == 3 * 3600
    assert patrol_control.parse_duration("半小时") == 1800
    assert patrol_control.parse_duration("1天") == 86400
    assert patrol_control.parse_duration("1分钟") == patrol_control.MIN_PAUSE
    assert patrol_control.parse_duration("30天") == patrol_control.MAX_PAUSE
    assert patrol_control.parse_duration("等她回来") == 0


def test_stop_for_a_while_then_resumes_by_itself(cam):
    cam.start_monitoring()
    text, notices = patrol_control.process("嗯[巡逻:停|3小时]", "aion")
    assert text == "嗯" and not cam.monitoring
    assert cam.cfg["patrol_resume_at"] == pytest.approx(time.time() + 3 * 3600, abs=5)
    assert cam.cfg["patrol_changed_by"] == "aion"
    assert "小克把巡逻停了 3 小时" in notices[0] and "自动恢复" in notices[0]
    assert asyncio.run(patrol_control.resume_when_due()) == ""  # 还没到点
    notice = asyncio.run(patrol_control.resume_when_due(now=time.time() + 3 * 3600 + 1))
    assert cam.monitoring and "小克" in notice and cam.cfg["patrol_resume_at"] == 0
    cam.stop_monitoring()


def test_stop_without_time_stays_off_until_someone_turns_it_on(cam):
    cam.start_monitoring()
    _, notices = patrol_control.process("[巡逻:停]", "ai3")
    assert not cam.monitoring and cam.cfg["patrol_resume_at"] == 0
    assert notices == ["🛡 狗蛋把巡逻停了，等有人再打开"]
    assert asyncio.run(patrol_control.resume_when_due(now=time.time() + 10 ** 7)) == ""
    assert "停着（狗蛋停的）" in patrol_control.status_text()
    _, notices = patrol_control.process("[巡逻:开]", "ai3")
    assert cam.monitoring and notices == ["🛡 狗蛋打开了巡逻"]
    cam.stop_monitoring()


def test_last_command_in_a_reply_wins(cam):
    _, notices = patrol_control.process("[巡逻:停][巡逻:开]", "aion")
    assert cam.monitoring and len(notices) == 1
    cam.stop_monitoring()


def test_failure_keeps_the_reply(cam, monkeypatch):
    def boom(**kw):
        raise RuntimeError("x")
    monkeypatch.setattr(cam, "stop_monitoring", boom)
    text, notices = patrol_control.process("晚安[巡逻:停]", "aion")
    assert text == "晚安" and "没成功" in notices[0]


def test_her_manual_switch_clears_the_ai_schedule(cam, monkeypatch):
    import routes.cam as cam_routes
    monkeypatch.setattr(cam_routes, "cam", cam)
    monkeypatch.setattr(cam_routes, "save_cam_config", lambda cfg: None)
    cam.stop_monitoring(resume_at=time.time() + 600)
    cam.cfg["patrol_changed_by"] = "aion"
    asyncio.run(cam_routes.cam_monitor_stop())
    assert cam.cfg["patrol_resume_at"] == 0 and cam.cfg["patrol_changed_by"] == ""
    asyncio.run(cam_routes.cam_monitor_start())
    assert cam.monitoring and cam.cfg["patrol_changed_by"] == ""
    cam.stop_monitoring()


def test_stop_then_start_right_away_runs_only_one_patrol(monkeypatch):
    """停了马上再开：旧线程还在 0.5 秒的小睡里，不能和新线程一起巡逻。"""
    monkeypatch.setattr(camera, "save_cam_config", lambda cfg: None)
    monitor = camera.CameraMonitor()
    monitor.cfg["auto_interval_min"] = monitor.cfg["auto_interval_max"] = 60  # 一小时后才截图：线程只在等
    monitor.start_monitoring()
    first = monitor._monitor_thread
    monitor.stop_monitoring()
    monitor.start_monitoring()
    second = monitor._monitor_thread
    first.join(timeout=2)
    assert not first.is_alive() and second.is_alive()
    monitor.stop_monitoring()
    second.join(timeout=2)


def test_ability_text_tells_the_ai_the_current_state(cam):
    cam.stop_monitoring(resume_at=time.time() + 3600)
    text = patrol_control.ability_text("小猫")
    assert "[巡逻:停|3小时]" in text and "[CAM_CHECK]" in text and "自动恢复" in text


def test_every_reply_path_handles_patrol_commands():
    """小克私聊（发送、编辑重发、重新生成）、聊天室（小克在群里、Lucien）、座位、后台回复（巡逻叫醒、闹铃、定位）。"""
    counts = {"routes/chat.py": 3, "routes/chatroom.py": 2, "schedule.py": 1}
    for name, count in counts.items():
        assert (HERE / name).read_text(encoding="utf-8").count("patrol_control.process(") == count, name
    caps = (HERE / "capabilities.py").read_text(encoding="utf-8")
    assert caps.count("patrol_control.ability_text(") == 2  # 小克 / Lucien 一份，座位一份
    assert "patrol_control.resume_loop()" in (HERE / "main.py").read_text(encoding="utf-8")
