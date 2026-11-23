import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes import chatroom as chatroom_routes
import ai_providers


class ConnorCodexUsageForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_connor_codex_stream_passes_usage_meta_to_cli_call(self):
        seen = {}

        async def fake_stream_connor_cli(*, messages, meta=None):
            seen["messages"] = messages
            seen["meta"] = meta
            if meta is not None:
                meta["prompt_tokens"] = 3
            yield "ok"

        messages = [{"role": "user", "content": "hello"}]
        meta = {}

        with (
            patch("routes.chatroom._resolve_connor_model", return_value="Codex"),
            patch("routes.chatroom.stream_connor_cli", new=fake_stream_connor_cli),
        ):
            chunks = [
                chunk
                async for chunk in chatroom_routes._stream_connor_model(messages, "Codex", meta)
            ]

        self.assertEqual(chunks, ["ok"])
        self.assertIs(seen["messages"], messages)
        self.assertIs(seen["meta"], meta)
        self.assertEqual(meta["prompt_tokens"], 3)

    def test_explicit_zero_is_preserved_as_reported_cache(self):
        meta = {}
        ai_providers._apply_codex_usage_meta(meta, {
            "input_tokens": 100, "cached_input_tokens": 0,
            "cache_write_input_tokens": 25, "output_tokens": 5,
            "reasoning_output_tokens": 0, "total_tokens": 105,
        })
        self.assertIn("cached_tokens", meta["raw"]["prompt_tokens_details"])
        self.assertEqual(meta["raw"]["prompt_tokens_details"]["cached_tokens"], 0)
        self.assertEqual(meta["raw"]["prompt_tokens_details"]["cache_creation_tokens"], 25)

    def test_late_zero_snapshot_does_not_erase_cached_tokens(self):
        meta = {}
        ai_providers._apply_codex_usage_meta(meta, {
            "input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 2, "total_tokens": 102,
        })
        ai_providers._apply_codex_usage_meta(meta, {
            "input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 3, "total_tokens": 103,
        })
        self.assertEqual(meta["raw"]["prompt_tokens_details"]["cached_tokens"], 60)
        self.assertEqual(meta["completion_tokens"], 3)


if __name__ == "__main__":
    unittest.main()
