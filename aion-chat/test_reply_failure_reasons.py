import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stream_safety import describe_failure, failure_detail_snippet  # noqa: E402

CASES = {
    "[错误] 还没有能用的线路：请到 设置 → 🧭 模型线路 接一条中转站": "没有可用的线路",
    "[错误] 模型线路已停用: claude-x": "没有可用的线路",
    "[Gemini错误 401] invalid api key": "钥匙无效",
    "Error code: 403 Forbidden": "钥匙无效",
    "[硅基流动错误 429] rate limit exceeded": "限流",
    "CodexAppServerError：usage limit reached": "限流",
    "candidate finishReason: SAFETY": "模型拒绝回答",
    "The model refused to answer": "模型拒绝回答",
    "Error code: 502 Bad Gateway": "线路那边出错了",
    "请求超时：ReadTimeout": "太久没有回应",
    "All connection attempts failed": "连不上线路",
    "[Errno 111] Connection refused": "连不上线路",  # 不能被当成「模型拒绝」
    "Server disconnected without sending a response.": "连不上线路",
}


def test_reasons_are_spelled_out():
    for detail, expected in CASES.items():
        assert expected in describe_failure(detail), detail


def test_unknown_errors_are_not_guessed():
    assert describe_failure("奇怪的东西") == "" and describe_failure(None) == ""


def test_snippet_hides_keys():
    text = failure_detail_snippet("Incorrect API key provided: sk-abcd1234567890xyz; Authorization: Bearer secret123")
    assert "1234567890" not in text and "secret123" not in text and "sk-abcd" in text


def test_chatroom_notice_uses_the_reason(monkeypatch):
    from routes import chatroom
    monkeypatch.setattr(chatroom, "_name_for_identity", lambda sender: "Lucien")
    assert chatroom._chatroom_reply_failure_text("connor", "[Gemini错误 429] rate limit") \
        == "Lucien 本次回复失败：线路限流或额度用完了，稍后再试（[Gemini错误 429] rate limit）"
    assert chatroom._chatroom_reply_failure_text("connor", "transport") == "Lucien 本次回复失败：连接中断"
    assert "没有成功返回" in chatroom._chatroom_reply_failure_text("connor", "")


def test_stream_transport_notice_names_the_reason():
    import asyncio
    from stream_safety import CHAT_STREAM_POLICY, consume_safe_stream

    async def broken():
        yield "你好"
        raise RuntimeError("[Errno 111] Connection refused")

    result = asyncio.run(consume_safe_stream(broken(), CHAT_STREAM_POLICY, lambda text: None))
    assert result.stop_reason == "transport"
    assert result.notice == "回复失败：连不上线路（中转站或网络连不通），已自动停止生成。"
