"""2026-10-28 小猫：每位 AI 都能自己停 / 开哨兵巡逻，停多久自己定（写时长到点恢复，不写就一直停）。"""

import asyncio
import re
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


# ── §88 复审 ──

def test_r1_queued_resume_cannot_undo_her_manual_stop(cam, monkeypatch):
    """到点恢复已经排进线程池、还没轮到；这时小猫手动关。放行后旧恢复不能再把巡逻打开。"""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import routes.cam as cam_routes
    monkeypatch.setattr(cam_routes, "cam", cam)
    cam.stop_monitoring(resume_at=time.time() - 1, changed_by="aion")  # 已经到点
    gate = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        pool = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(pool)
        loop.run_in_executor(None, gate.wait)          # 先把唯一的线程占住
        resume = asyncio.create_task(patrol_control.resume_when_due())
        await asyncio.sleep(0.05)                       # 恢复已经读完状态、排进线程池
        stopped = await cam_routes.cam_monitor_stop()  # 小猫手动关
        gate.set()
        notice = await resume
        return stopped, notice
    stopped, notice = asyncio.run(scenario())
    assert stopped["monitoring"] is False
    assert notice == "" and not cam.monitoring and cam.cfg["patrol_resume_at"] == 0


def test_r1_ai_changing_the_pause_voids_the_old_resume(cam):
    cam.stop_monitoring(resume_at=time.time() - 1, changed_by="aion")
    version, resume_at = cam.patrol_snapshot()
    patrol_control.process("[巡逻:停|3小时]", "aion")      # 到点前一刻又延长
    assert cam.resume_if_due(version, resume_at) is False and not cam.monitoring
    patrol_control.process("[巡逻:开]", "aion")
    version, _ = cam.patrol_snapshot()
    cam.stop_monitoring(changed_by="")                      # 手动开了又关：不自动恢复
    assert cam.resume_if_due(version, 0) is False and not cam.monitoring


def test_r1_two_overlapping_due_checks_resume_once(cam, monkeypatch):
    starts = []
    real_start = cam.start_monitoring
    monkeypatch.setattr(cam, "start_monitoring", lambda changed_by=None: (starts.append(1), real_start(changed_by))[1])
    cam.stop_monitoring(resume_at=time.time() - 1, changed_by="ai3")

    async def both():
        return await asyncio.gather(patrol_control.resume_when_due(), patrol_control.resume_when_due())
    notices = asyncio.run(both())
    assert sorted(bool(n) for n in notices) == [False, True] and len(starts) == 1 and cam.monitoring
    cam.stop_monitoring()


def test_r2_old_patrol_does_not_submit_its_late_screenshot(monkeypatch):
    """旧一代正在等手机拍照；停了马上再开（新一代）；旧照片回来后不能再交去分析。"""
    import threading
    monkeypatch.setattr(camera, "save_cam_config", lambda cfg: None)
    monkeypatch.setattr(camera, "save_monitor_screenshot", lambda *a, **k: None)
    gate, waiting, submitted = threading.Event(), threading.Event(), []

    def slow_capture(kind, should_continue=None):
        waiting.set()
        gate.wait(3)
        return camera.MonitorImageResult(status="ok", source="phone", request_id="r", jpeg=b"x")

    class Done:
        def result(self, timeout=None):
            return None

    def record(coro, loop):
        submitted.append(coro.cr_code.co_name)
        coro.close()
        return Done()
    monkeypatch.setattr(camera, "acquire_monitor_image_sync", slow_capture)
    monkeypatch.setattr(camera.asyncio, "run_coroutine_threadsafe", record)
    monitor = camera.CameraMonitor()
    monitor._loop = object()
    monitor.cfg.update(active_source="phone", auto_interval_min=60, auto_interval_max=60, quiet_hours_enabled=False)
    monitor.start_monitoring()
    first = monitor._monitor_thread
    monitor._next_capture_at = 0                  # 让旧一代马上去拍
    assert waiting.wait(3)
    monitor.stop_monitoring()
    monitor.start_monitoring()                    # 新一代（一小时后才拍）
    gate.set()
    first.join(timeout=3)
    assert not first.is_alive()
    assert "_analyze_and_log" not in submitted
    monitor.stop_monitoring()
    monitor._monitor_thread.join(timeout=2)


def test_r2_analysis_from_a_stale_patrol_stops_before_calling_the_sentinel(monkeypatch):
    monkeypatch.setattr(camera, "save_cam_config", lambda cfg: None)
    monitor = camera.CameraMonitor()
    monitor._monitor_loop = lambda gen=None: None
    monitor.start_monitoring()
    stale = monitor._monitor_gen
    monitor.stop_monitoring()
    monitor.start_monitoring()
    # 截图文件根本不存在：如果没先核对代数，会去找文件；核对了就直接返回
    assert asyncio.run(monitor._analyze_and_log("不存在.jpg", None, patrol_gen=stale)) is None
    text = (HERE / "camera.py").read_text(encoding="utf-8")
    sentinel = text.index("raw_text = await _call_sentinel_vision(scfg, prompt, img_b64, timeout=60)")
    assert "not self.patrol_active(patrol_gen)" in text[sentinel - 300:sentinel]
    monitor.stop_monitoring()


def test_r3_only_a_patrol_command_is_not_an_empty_reply(cam, monkeypatch):
    import routes.chatroom as chatroom_routes
    notices = []

    async def sys_msg(room_id, text, _q, after_msg_id=None):
        notices.append(text)
        return {"id": "sys"}
    monkeypatch.setattr(chatroom_routes, "_chatroom_sys_msg", sys_msg)
    monkeypatch.setattr(chatroom_routes, "_name_for_identity", lambda who: "狗蛋")
    text, triggered = asyncio.run(chatroom_routes._process_seat_commands("[巡逻:停]", "s1", "ai3", "m1", asyncio.Queue()))
    assert text.strip() == "" and triggered.get("patrol") and notices
    # 座位：triggered 非空 → 走 seat_done(message=None)，不记 empty_response
    src = (HERE / "routes" / "chatroom.py").read_text(encoding="utf-8")
    seat = src[src.index("    if not clean_text:\n        if side_effect_notice or triggered:"):]
    assert seat.index("seat_done") < seat.index('"empty_response"')
    # Lucien / 小克在群里：只写巡逻指令不存空消息
    text, triggered = asyncio.run(chatroom_routes._process_chatroom_commands("[巡逻:开]", "g1", "connor", "m2", asyncio.Queue()))
    assert text.strip() == "" and triggered.get("patrol")
    assert src.count('(triggered.get("memory_search") or triggered.get("patrol")) and not clean_text') == 3
    cam.stop_monitoring()


def test_r4_xiaoke_patrol_runs_before_the_memory_search_early_return():
    src = (HERE / "routes" / "chat.py").read_text(encoding="utf-8")
    patrol = [m.start() for m in re.finditer(r"patrol_control\.process\(", src)]
    search = [m.start() for m in re.finditer(r"await _start_private_memory_search_if_requested\(", src)]
    assert len(patrol) == len(search) == 3
    for p, s in zip(patrol, search):
        assert p < s and s - p < 600  # 紧挨在翻记忆之前


def test_l1_unknown_patrol_words_are_removed_from_the_reply(cam):
    assert patrol_control.process("晚安[巡逻:跳舞]", "aion") == ("晚安", [])
    assert patrol_control.process("[巡逻:跳舞]好[巡逻：飞]", "aion") == ("好", [])
    assert not cam.monitoring
