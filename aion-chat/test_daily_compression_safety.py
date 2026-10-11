import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RetiredDailyCompressionTests(unittest.TestCase):
    def test_legacy_routes_reject_cached_clients_without_touching_data(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routes import memories

        app = FastAPI()
        app.include_router(memories.router)
        with patch.object(memories, "get_db", side_effect=AssertionError("must not open DB")):
            with TestClient(app) as client:
                for method, path in (
                    ("POST", ""), ("GET", "/latest"), ("POST", "/old/apply"),
                    ("PATCH", "/old"), ("POST", "/old/discard"),
                ):
                    with self.subTest(method=method, path=path):
                        response = client.request(method, "/api/memories/compress-daily" + path)
                        self.assertEqual(response.status_code, 410)

    def test_frontends_no_longer_load_or_submit_legacy_drafts(self):
        for filename in ("memory.html", "chatroom.js"):
            with self.subTest(filename=filename):
                source = (ROOT / "static" / filename).read_text(encoding="utf-8")
                self.assertFalse("/api/memories/compress-daily" in source, filename)


class DigestBatchingTests(unittest.TestCase):
    def test_digest_batches_stay_between_twenty_and_fifty(self):
        from memory import _split_into_groups

        for total in (51, 59, 69, 99, 101, 119, 151):
            groups = _split_into_groups(list(range(total)))
            sizes = [len(group) for group in groups]
            self.assertEqual(sum(sizes), total)
            self.assertLessEqual(max(sizes), 50, (total, sizes))
            self.assertGreaterEqual(min(sizes), 20, (total, sizes))

    def test_digest_batch_boundaries_are_fifty_and_twenty(self):
        from memory import _split_into_groups

        self.assertEqual([len(group) for group in _split_into_groups(list(range(50)))], [50])
        self.assertEqual([len(group) for group in _split_into_groups(list(range(51)))], [26, 25])
        self.assertEqual([len(group) for group in _split_into_groups(list(range(100)))], [50, 50])

    def test_digest_thresholds_and_prompt_match_new_policy(self):
        memory_source = (ROOT / "memory.py").read_text(encoding="utf-8")
        chatroom_source = (ROOT / "chatroom.py").read_text(encoding="utf-8")

        self.assertIn("min_messages=40", memory_source)
        self.assertIn("if len(msgs) < 40", chatroom_source)
        self.assertIn("至少需要 40 条", chatroom_source)
        self.assertIn("每 50 条消息通常产出 1-3 条 daily", memory_source)


if __name__ == "__main__":
    unittest.main()
