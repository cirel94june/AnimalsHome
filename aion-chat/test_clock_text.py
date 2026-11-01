"""11-01 小猫：小克下午写的日记以为是凌晨、以为今天是工作日。给 AI 的时间要写清星期几、时段、上不上班。"""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import clock_text

ROOT = Path(__file__).resolve().parent


class ClockTextTests(unittest.TestCase):
    def test_sunday_afternoon_says_so(self):
        text = clock_text.now_text(datetime(2026, 11, 1, 17, 33, 5))
        self.assertEqual("2026年11月1日 星期日 下午 17:33:05（北京时间，今天是休息日）", text)

    def test_periods_and_workdays(self):
        self.assertIn("星期一 凌晨 03:00", clock_text.now_text(datetime(2026, 11, 2, 3, 0), seconds=False))
        self.assertIn("今天是工作日", clock_text.now_text(datetime(2026, 11, 2, 9, 0)))
        self.assertIn("晚上", clock_text.now_text(datetime(2026, 11, 2, 21, 0)))

    def test_falls_back_to_weekends_when_calendar_is_unavailable(self):
        with patch.dict("sys.modules", {"chinese_calendar": None}):
            self.assertEqual("休息日", clock_text.day_kind(datetime(2099, 1, 3).date()))  # 星期六
            self.assertEqual("工作日", clock_text.day_kind(datetime(2099, 1, 5).date()))

    def test_every_prompt_uses_it(self):
        for name in ("context_builder.py", "routes/chat.py", "schedule.py", "seat_chat.py", "memory.py",
                     "chatroom.py", "gift.py", "seat_diary.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("from clock_text import now_text", source, name)
            self.assertNotIn('strftime("%Y年%m月%d日  %H:%M:%S")', source, name)
        # 座位日记原来连当前时间都没有
        self.assertIn('f"当前时间：{now_text()}', (ROOT / "seat_diary.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
