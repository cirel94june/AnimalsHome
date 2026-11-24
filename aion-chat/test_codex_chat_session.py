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
