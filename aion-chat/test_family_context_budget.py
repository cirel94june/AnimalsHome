import asyncio
from unittest.mock import AsyncMock
import context_builder as c
import autonomy as a
import routes.activity as activity
import memory_hub_jobs as jobs

def test_history_budget_only_removes_padding_not_base(tmp_path,monkeypatch):
    import cache_window_hints as hints
    monkeypatch.setattr(hints,"PATH",tmp_path/"hints.json")
    monkeypatch.setattr(hints,"_HINTS",{})
    monkeypatch.setattr(c,"_WINDOW_STARTS",{})
    monkeypatch.setenv("AIONSHOME_LIGHT_HISTORY_PADDING","1")
    rows=[{"id":str(i),"source":"group","created_at":i,"content":"字"*420} for i in range(30)]
    key=("synthetic",15)
    assert c._stable_window(rows[:15],15,key)==rows[:15]
    assert c._stable_window(rows[:16],15,key)==rows[1:16]
    big={**rows[16],"content":"字"*10000}
    window=c._stable_window(rows[2:16]+[big],15,key)
    assert len(window)==15 and window[-1]==big
    monkeypatch.setenv("AIONSHOME_LIGHT_HISTORY_PADDING","0")
    key=("off",15)
    c._stable_window(rows[:15],15,key)
    assert c._stable_window(rows[:20],15,key)==rows[:20]

def test_home_browsing_can_be_silent_without_extra_diary(monkeypatch):
    monkeypatch.setattr(a,"_actor_label",lambda actor:actor)
    monkeypatch.setattr(a,"_home_dynamics_snapshot",AsyncMock(return_value=("public timeline",[])))
    ask=AsyncMock(return_value={"after_read_action":"read_only","reason":"看看"})
    monkeypatch.setattr(a,"_ask_actor_json",ask)
    event=AsyncMock(return_value={"id":"e"})
    monkeypatch.setattr(a,"append_idle_event",event)
    diary=AsyncMock(side_effect=AssertionError("no diary"))
    monkeypatch.setattr(a,"_write_home_dynamics_diary",diary)
    assert asyncio.run(a._run_home_dynamics("ai3"))=={"event":{"id":"e"}}
    assert "read_only" in ask.await_args.args[1]
    ask.assert_awaited_once()
    diary.assert_not_called()
    ask.return_value={"after_read_action":"write_diary"}
    assert asyncio.run(a._run_home_dynamics("ai3"))=={"event":{"id":"e"}}
    diary.assert_not_called()

def test_confirmed_handoff_notice_visible_to_both_timeline_readers():
    row={"action":"hub_handoff_saved","result_type":"","result_id":"","metadata":"{}",
         "actor":"ai3","title":"狗蛋整理并保存了私聊交接卡"}
    assert a._idle_event_home_title(row,set(),set())==row["title"]
    assert activity._idle_event_timeline_title(row,"狗蛋",set(),set())==row["title"]

def test_handoff_notice_only_after_hub_save(monkeypatch):
    import memory_hub_bridge as bridge
    import actors
    from contextlib import closing
    import sqlite3
    # In-memory bridge test fixture; the write helper does not need actual chat.
    conn=sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE recent_turns(actor text,user_message text,ai_response text,created_at real,id integer)")
    monkeypatch.setattr(bridge,"load_config",lambda:{})
    monkeypatch.setattr(bridge,"source_ai_for",lambda *a:"jasper")
    monkeypatch.setattr(jobs,"pin_background_model",lambda a:("model",{}))
    monkeypatch.setattr(bridge,"_outbox",lambda:conn)
    monkeypatch.setattr(jobs,"_session_turns",lambda *a:[{"user_message":"private","ai_response":"private reply"}])
    monkeypatch.setattr(jobs,"_handoff_prompt",lambda *a:[])
    monkeypatch.setattr(jobs,"still_own_model",lambda *a:True)
    monkeypatch.setattr(jobs,"complete",AsyncMock(return_value="secret card"))
    monkeypatch.setattr(jobs,"model_label",lambda m:m)
    monkeypatch.setattr(jobs,"_mark_handoff",lambda *a,**k:None)
    monkeypatch.setattr(actors,"display_name",lambda a:"狗蛋")
    note=AsyncMock()
    monkeypatch.setattr(a,"append_idle_event",note)
    monkeypatch.setattr(bridge,"_call_tool",AsyncMock(return_value='{"status":"saved"}'))
    assert asyncio.run(jobs.write_handoff("ai3",100,now=101))=="saved"
    note.assert_awaited_once()
    assert note.await_args.args[0:2]==("ai3","hub_handoff_saved")
    assert "secret" not in str(note.await_args)
