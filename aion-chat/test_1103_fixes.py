"""11-03 小猫报的几处：换了模型还用旧渠道、红点要重进 App 才消、朋友圈 AI 之间不互相回、静默时段上午下午看错。"""

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"


class ModelSingleSourceTests(unittest.TestCase):
    def test_group_chat_uses_the_saved_model_not_the_stale_page_one(self):
        from routes import chatroom as routes
        body = SimpleNamespace(model="[kiro量高缓]claude-opus-4-6-thinking", connor_model="old-connor")
        with patch.object(routes, "load_chatroom_config",
                          return_value={"aion_model": "[特价纯血]claude-opus-4-6-thinking", "connor_model": "Codex-5.6-Sol"}), \
                patch.object(routes, "aion_model_key", side_effect=lambda k: k), \
                patch.object(routes, "_resolve_connor_model", side_effect=lambda k: k):
            self.assertEqual(("[特价纯血]claude-opus-4-6-thinking", "Codex-5.6-Sol"), routes._room_models(body))
            with patch.object(routes, "load_chatroom_config", return_value={}):
                self.assertEqual((body.model, body.connor_model), routes._room_models(body))  # 没存过才用页面的

    def test_changing_model_elsewhere_updates_the_ai_card(self):
        import actors
        saved = {}
        data = {"seats": {"aion": {"model": "旧模型"}}}
        with patch.object(actors, "_load_raw", return_value=data), \
                patch.object(actors, "_save_raw", side_effect=lambda d: saved.update(d)):
            actors.set_own_model_from_elsewhere("aion", "新模型")
            actors.set_own_model_from_elsewhere("ai3", "别人的")  # 座位不走这里
        self.assertEqual("新模型", saved["seats"]["aion"]["model"])
        self.assertNotIn("ai3", saved["seats"])

    def test_private_chat_and_conversation_list_follow_the_ai_card(self):
        source = (ROOT / "routes" / "chat.py").read_text(encoding="utf-8")
        self.assertEqual(3, source.count('model_key = private_model_key(aion_own_model() or (conv["model"] if conv else ""))'))
        self.assertIn('data["model"] = private_model_key(aion_own_model() or data.get("model"))', source)
        self.assertIn('actors.set_own_model_from_elsewhere("aion", model)', source)


class UnreadRefreshTests(unittest.TestCase):
    def test_read_marks_refresh_the_other_pages_at_once(self):
        chat = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("refreshUnreadInSubPages();", chat)
        self.assertIn("window.refreshUnread = refreshUnread;", chat)
        self.assertIn("win?.checkMomentsUnread?.();", chat)
        self.assertIn("window.top?.refreshUnread?.()", (STATIC / "chatroom.js").read_text(encoding="utf-8"))
        self.assertIn("window.top?.refreshUnread?.()", (STATIC / "moments.html").read_text(encoding="utf-8"))
        self.assertIn("window.onAionUnreadChanged = ", (STATIC / "contacts.js").read_text(encoding="utf-8"))


class MomentsReplyTests(unittest.TestCase):
    def test_author_answers_an_ai_comment_once(self):
        source = (ROOT / "routes" / "moments.py").read_text(encoding="utf-8")
        self.assertIn('if target_comment_id is None and author not in (None, "", "user", who) and author in _ai_authors():', source)
        self.assertIn("spawn_generation_task(_ai_reply_to_moment(author, moment_id, comment_id))", source)


class QuietSayTests(unittest.TestCase):
    def test_quiet_hours_are_spelled_out_with_morning_and_evening(self):
        html = (STATIC / "switches.html").read_text(encoding="utf-8").replace("\r\n", "\n")
        start = html.index("  function clockSay(")
        end = html.index("  function refreshQuietSay(")
        script = html[start:end] + """
const els = {}; const $ = id => (els[id] ||= { textContent: '', classList: { remove() {}, toggle(_, on) { els[id].warn = on; } } });
quietSay('a', true, '11:00', '08:00'); quietSay('b', true, '23:00', '08:00'); quietSay('c', false, '23:00', '08:00');
console.log(JSON.stringify([els.a.textContent, els.a.warn, els.b.textContent, els.b.warn, els.c.textContent]));
"""
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
        a, a_warn, b, b_warn, c = json.loads(out)
        self.assertEqual("从上午 11:00到第二天上午 8:00，一共 21 小时。这段比半天还长，确认一下上午下午没有选反？", a)
        self.assertTrue(a_warn)
        self.assertEqual("从深夜 11:00到第二天上午 8:00，一共 9 小时", b)
        self.assertFalse(b_warn)
        self.assertEqual("", c)


if __name__ == "__main__":
    unittest.main()
