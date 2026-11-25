import asyncio
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch
import aiosqlite
import daily_diary as d

class DailyDiaryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name)/"test.db"
        self.p = patch.object(d,"get_db",lambda: aiosqlite.connect(self.db))
        self.p.start()
        async with d.get_db() as db:
            await db.execute("CREATE TABLE diary_entries(author TEXT,created_at REAL,source_type TEXT)")
            await db.commit()
        self.now = datetime(2026,11,24,14,tzinfo=d.TZ).timestamp()
    async def asyncTearDown(self):
        self.p.stop()
        self.tmp.cleanup()
    def test_policy(self):
        self.assertFalse(d.eligible(self.now,self.now-1801,True))
        self.assertFalse(d.eligible(self.now-8*3600,self.now-8*3600-1801,True))
        evening = self.now+4*3600
        self.assertTrue(d.eligible(evening,evening-1801,True))
        self.assertFalse(d.eligible(evening,evening-60,True))
        self.assertFalse(d.eligible(self.now,self.now-60,True))
        self.assertFalse(d.eligible(self.now,0,False))
        self.assertTrue(d.eligible(self.now+7*3600,0,False))
        self.assertFalse(d.eligible(self.now+7*3600,self.now+7*3600-60,True))
        self.assertTrue(d.eligible(self.now+9*3600,self.now+9*3600-60,True))
    def test_balanced_bucket_keeps_early_and_late_rows(self):
        lines=[f"row {i} " + "字"*220 for i in range(16)]
        chosen=d.balanced_lines(lines,2600)
        self.assertEqual(chosen[0],lines[0])
        self.assertEqual(chosen[-1],lines[-1])
        self.assertLessEqual(len("\n".join(chosen)),2600)

    async def test_same_budget_all_actors(self):
        for a in ("aion","connor","ai3"):
            self.assertTrue(await d._claim(a,self.now))
            self.assertFalse(await d._claim(a,self.now+600))
            self.assertTrue(await d._claim(a,self.now+3601))
            self.assertFalse(await d._claim(a,self.now+7200))
            self.assertTrue(await d._claim(a,self.now+86400))
    async def test_concurrent_claim(self):
        r=await asyncio.gather(d._claim("connor",self.now),d._claim("connor",self.now))
        self.assertEqual(sum(r),1)
    async def test_legacy_today_counts_yesterday_does_not(self):
        async with d.get_db() as db:
            await db.executemany("INSERT INTO diary_entries VALUES(?,?,?)",[
                ("aion",self.now-1,"memory_digest"),("connor",self.now-86400,"chatroom_digest"),
                ("ai3",self.now-1,"seat_digest")])
            await db.commit()
        self.assertFalse(await d._claim("aion",self.now))
        self.assertTrue(await d._claim("connor",self.now))
        self.assertFalse(await d._claim("ai3",self.now))
