import unittest
from unittest.mock import patch
import codex_chat_session as s

def m(text, role="user", tag=None):
    x={"role":role,"content":text}
    if tag:x["_context"]=tag
    return x

def context(*rows, clock="today", persona="fox", memory="old memory"):
    return [m(persona),m(memory,tag="memory"),m(clock,tag="live"),
            m("[历史记录说明]",tag="history")]+[m(x) for x in rows]

def row(text):
    return "历史消息 - speaker："+text

def plan(meta, messages, model="model"):
    s.attach(meta,messages)
    return s.prepare(meta,model=model,command=["codex"],instruction_revision="base",
                     full_prompt="full prompt",live_heading="LIVE")

class SessionTests(unittest.TestCase):
    def setUp(self):
        s._states.clear();s._generations.clear()
    def first(self, room="a", kind="private", **kw):
        meta={}
        s.begin(meta,room,kind,30)
        p=plan(meta,context(row("first"),**kw))
        self.assertIsNotNone(p);self.assertEqual(p.thread_id,"")
        s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},"answer")
        self.assertTrue(s.confirm(meta,{"sender":"connor","room_id":room,"content":"answer"},m(row("answer"))))
    def test_delta_uses_confirmed_reply_and_new_context(self):
        self.first()
        meta={};s.begin(meta,"a","private",30)
        p=plan(meta,context(row("first"),row("answer"),row("next"),clock="now",memory="new memory"))
        self.assertEqual(p.thread_id,"tid")
        self.assertIn("new memory",p.prompt);self.assertIn(row("next"),p.prompt)
        self.assertNotIn(row("first"),p.prompt);self.assertNotIn(row("answer"),p.prompt)
        self.assertEqual(p.previous_usage["inputTokens"],100)
    def test_interleaved_history_before_confirmed_reply_restarts(self):
        self.first();meta={};s.begin(meta,"a","private",30)
        self.assertEqual(plan(meta,context(row("first"),row("interleaved"),row("answer"),row("next"))).thread_id,"")
    def test_non_codex_debug_does_not_leak_internal_state(self):
        import json
        from routes.chatroom import _chatroom_debug_payload
        meta={};s.begin(meta,"a","private",30)
        s.attach(meta,context(row("secret")))
        payload=_chatroom_debug_payload(room_id="a",model_key="other",msg_id="x",
            history=[],digest_result={},usage_meta=meta)
        self.assertNotIn("_codex_session_",json.dumps(payload))
        self.assertNotIn("secret",json.dumps(payload))
    def test_edit_trim_or_missing_reply_restarts(self):
        for rows in [(row("edited"),row("answer"),row("next")),
                     (row("answer"),row("next")),
                     (row("first"),row("different"),row("next"))]:
            self.first();meta={};s.begin(meta,"a","private",30)
            self.assertEqual(plan(meta,context(*rows)).thread_id,"")
    def test_model_persona_scope_and_limit_reset(self):
        for room,kind,limit,model,persona in [
            ("b","private",30,"model","fox"),("a","group",30,"model","fox"),
            ("a","private",40,"model","fox"),("a","private",30,"new","fox"),
            ("a","private",30,"model","new fox")]:
            self.first();meta={};s.begin(meta,room,kind,limit)
            self.assertEqual(plan(meta,context(row("first"),row("answer"),row("next"),persona=persona),model).thread_id,"")
    def test_unconfirmed_and_disabled_scope(self):
        self.first();meta={};s.begin(meta,"a","private",30,allowed=False)
        self.assertIsNone(plan(meta,context(row("first"))))
        meta={};s.begin(meta,"a","private",30)
        self.assertEqual(plan(meta,context(row("first"),row("answer"),row("next"))).thread_id,"")
    def test_real_renderer_matches_saved_reply(self):
        from context_builder import render_merged_timeline
        a={"id":"a","source":"private","sender":"user","content":"hello","created_at":100}
        saved={"id":"b","room_id":"a","source":"private","sender":"connor","content":"answer",
               "created_at":101,"attachments":[{"type":"reply_usage"}]}
        c={"id":"c","source":"private","sender":"user","content":"next","created_at":102}
        def build(rows,clock):
            return [m("fox"),m(clock,tag="live")]+render_merged_timeline(rows,"connor")
        meta={};s.begin(meta,"a","private",30);p=plan(meta,build([a],"today"))
        s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},"answer")
        self.assertTrue(s.confirm_saved(meta,saved,"private"))
        meta={};s.begin(meta,"a","private",30);p=plan(meta,build([a,saved,c],"later"))
        self.assertEqual(p.thread_id,"tid")
        self.assertIn("next",p.prompt)
    def test_confirmed_pat_matches_real_folded_transcript(self):
        from context_builder import render_merged_timeline
        a={"id":"a","source":"private","sender":"user","content":"hello","created_at":100}
        saved={"id":"b","room_id":"a","source":"private","sender":"connor","content":"answer next","created_at":102}
        event={"id":"pat_x","room_id":"a","source":"private","sender":"system","content":"notice",
               "created_at":101,"attachments":[{"type":"pat","actor":"connor","text":"摸摸猫"},
               {"type":"system_model_context"},{"type":"system_notice_order","after_msg_id":"b","inline_offset":6}]}
        c={"id":"c","source":"private","sender":"user","content":"next question","created_at":103}
        def build(rows, clock):
            return [m("fox"),m(clock,tag="live")]+render_merged_timeline(rows,"connor")
        for notices, expected in [((),False),([event],True)]:
            meta={};s.begin(meta,"a","private",30);p=plan(meta,build([a],"today"))
            s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},"answer[PAT:摸摸猫] next")
            self.assertEqual(s.confirm_saved(meta,saved,"private",notices=notices),expected)
        meta={};s.begin(meta,"a","private",30)
        p=plan(meta,build([a,event,saved,c],"later"))
        self.assertEqual(p.thread_id,"tid")
        self.assertIn("next question",p.prompt)
        self.assertNotIn("[PAT:摸摸猫]",p.prompt)
        self.assertEqual(meta["codex_session_reason"],"validated_continuation")
    def test_wrong_pat_or_other_changed_text_never_confirms(self):
        saved={"id":"b","room_id":"a","source":"private","sender":"connor","content":"answer","created_at":102}
        event={"id":"pat_x","room_id":"a","source":"private","sender":"system","content":"notice",
               "created_at":101,"attachments":[{"type":"pat","actor":"connor","text":"wrong action"},
               {"type":"system_model_context"},{"type":"system_notice_order","after_msg_id":"b","inline_offset":6}]}
        for raw in ("answer[PAT:other action]","answer[PAT:wrong action][WEB_SEARCH:x]"):
            meta={};s.begin(meta,"a","private",30);p=plan(meta,context(row("first")))
            s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},raw)
            self.assertFalse(s.confirm_saved(meta,saved,"private",notices=[event]))
    def test_live_profile_updates_without_changing_fixed_permissions(self):
        import asyncio
        from unittest.mock import AsyncMock
        import context_builder as cb
        import post_sentinel
        with patch.object(cb,"build_capability_prompt_items",AsyncMock(return_value=["fixed ability"])), \
             patch.object(cb,"is_capability_enabled",return_value=False), \
             patch.object(cb,"get_device_context_for_prompt",return_value=""), \
             patch.object(cb,"build_cli_file_storage_text",return_value=""), \
             patch.object(post_sentinel,"build_context_parts",AsyncMock(side_effect=[("profile A","state A"),("profile B","state B")])):
            first=asyncio.run(cb.build_ability_block("cat",who="connor"))
            second=asyncio.run(cb.build_ability_block("cat",who="connor"))
        self.assertEqual(first.split(cb.LIVE_STATE_HEADING)[0],second.split(cb.LIVE_STATE_HEADING)[0])
        self.assertIn("profile B",second.split(cb.LIVE_STATE_HEADING)[1])
    def test_attachments_fall_back(self):
        self.first();meta={};s.begin(meta,"a","private",30)
        messages=context(row("first"),row("answer"),row("next"))
        messages[-1]["attachments"]=["a.png"]
        self.assertIsNone(plan(meta,messages))
    def test_mismatched_save_never_confirms(self):
        meta={};s.begin(meta,"a","private",30);p=plan(meta,context(row("first")))
        s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},"raw")
        self.assertFalse(s.confirm(meta,{"sender":"connor","room_id":"a","content":"clean"},m(row("clean"))))
        self.assertFalse(s._states)
    def test_concurrent_newer_attempt_invalidates_old(self):
        meta={};s.begin(meta,"a","private",30);p=plan(meta,context(row("first")))
        other={};s.begin(other,"a","private",30)
        s.stage(meta,p,"tid",{"inputTokens":100,"outputTokens":5},"answer")
        self.assertFalse(s.confirm(meta,{"sender":"connor","room_id":"a","content":"answer"},m(row("answer"))))
    def test_expired_and_bounded(self):
        self.first()
        with patch.object(s.time,"monotonic",return_value=s._states[("a","private")].saved_at+s.IDLE_SECONDS+1):
            meta={};s.begin(meta,"a","private",30)
            self.assertEqual(plan(meta,context(row("first"),row("answer"),row("next"))).thread_id,"")
        self.first();s._states[("a","private")].turns=s.MAX_TURNS
        meta={};s.begin(meta,"a","private",30)
        self.assertEqual(plan(meta,context(row("first"),row("answer"),row("next"))).thread_id,"")
