import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_providers import ThinkTagReasoningFilter, _apply_codex_usage_meta, extract_think_tag_reasoning


class ThinkTagReasoningTests(unittest.TestCase):
    def test_moves_standard_think_block_out_of_visible_content(self):
        visible, reasoning = extract_think_tag_reasoning(
            "<think>\ninternal plan\n</think>\n\nVisible reply.",
            "",
        )

        self.assertEqual(visible, "Visible reply.")
        self.assertEqual(reasoning, "internal plan")

    def test_appends_multiple_think_blocks_to_existing_reasoning(self):
        visible, reasoning = extract_think_tag_reasoning(
            "Lead.\n<think>first</think>\nMiddle.\n<think>\nsecond\n</think>\nTail.",
            "native reasoning",
        )

        self.assertEqual(visible, "Lead.\n\nMiddle.\n\nTail.")
        self.assertEqual(reasoning, "native reasoning\n\nfirst\n\nsecond")

    def test_leaves_text_without_standard_think_tags_unchanged(self):
        visible, reasoning = extract_think_tag_reasoning(
            "Visible <thought>not this helper</thought> reply.",
            "native reasoning",
        )

        self.assertEqual(visible, "Visible <thought>not this helper</thought> reply.")
        self.assertEqual(reasoning, "native reasoning")

    def test_stream_filter_handles_tags_split_across_chunks(self):
        meta = {}
        filt = ThinkTagReasoningFilter(meta)

        visible = "".join([
            filt.feed("Hi <thi"),
            filt.feed("nk>secret</thi"),
            filt.feed("nk> there"),
            filt.flush(),
        ])

        self.assertEqual(visible, "Hi  there")
        self.assertEqual(meta.get("reasoning_content"), "secret")


class CodexUsageMetaTests(unittest.TestCase):
    def test_maps_codex_cli_usage_to_system_log_token_shape(self):
        meta = {}

        _apply_codex_usage_meta(
            meta,
            {
                "input_tokens": 100,
                "cached_input_tokens": 40,
                "output_tokens": 25,
                "reasoning_output_tokens": 7,
            },
        )

        self.assertEqual(meta["prompt_tokens"], 100)
        self.assertEqual(meta["completion_tokens"], 25)
        self.assertEqual(meta["total_tokens"], 125)
        self.assertEqual(meta["raw"]["prompt_tokens_details"]["cached_tokens"], 40)
        self.assertEqual(meta["raw"]["completion_tokens_details"]["reasoning_tokens"], 7)


if __name__ == "__main__":
    unittest.main()



def test_thinking_tags_are_hidden_too():
    """11-07：小克在群里漏了思维链，那条线路用的是 <thinking>。"""
    import ai_providers
    meta = {}
    stream = ai_providers.ThinkTagReasoningFilter(meta)
    shown = "".join(stream.feed(part) for part in ["<thin", "king>想一想", "要说什么</thin", "king>你好呀"]) + stream.flush()
    assert shown == "你好呀" and meta["reasoning_content"] == "想一想要说什么"
    visible, reasoning = ai_providers.extract_think_tag_reasoning("<thinking>想</thinking>好的")
    assert visible == "好的" and reasoning == "想"


def test_think_tags_still_work_and_mismatched_tags_do_not_close():
    import ai_providers
    stream = ai_providers.ThinkTagReasoningFilter({})
    assert stream.feed("<think>甲</thinking>乙</think>丙") + stream.flush() == "丙"
    stream = ai_providers.ThinkTagReasoningFilter({})
    assert stream.feed("<thinking>甲</think>乙</thinking>丙") + stream.flush() == "丙"
    stream = ai_providers.ThinkTagReasoningFilter({})
    assert stream.feed("我在想") + stream.flush() == "我在想"
