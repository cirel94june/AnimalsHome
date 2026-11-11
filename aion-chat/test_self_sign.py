import actors
import self_sign


def setup(monkeypatch):
    saved = []
    monkeypatch.setattr(actors, "set_sign", lambda actor, sign: saved.append((actor, sign)) or True)
    monkeypatch.setattr(self_sign, "_changes", {})
    return saved


def test_tag_is_hidden_and_saved(monkeypatch):
    saved = setup(monkeypatch)
    assert self_sign.process("早呀\n[签名：今天也在想你]", "aion") == "早呀"
    assert saved == [("aion", "今天也在想你")]


def test_not_allowed_only_strips(monkeypatch):
    saved = setup(monkeypatch)
    assert self_sign.process("嗯 [签名:x]", "ai3", allowed=False) == "嗯"
    assert saved == []


def test_cleaning_and_daily_limit(monkeypatch):
    saved = setup(monkeypatch)
    self_sign.process("[签名:<b>" + "长" * 80 + "]", "aion", now=1)
    assert "<" not in saved[0][1] and len(saved[0][1]) <= self_sign.MAX_CHARS
    for i in range(5):
        self_sign.process(f"[签名:第{i}个]", "aion", now=2 + i)
    assert len(saved) == self_sign.MAX_PER_DAY
    self_sign.process("[签名:第二天]", "aion", now=90000)
    assert saved[-1] == ("aion", "第二天")


def test_plain_text_untouched(monkeypatch):
    setup(monkeypatch)
    assert self_sign.process("我想换个签名", "aion") == "我想换个签名"


def test_games_switch_blocks_only_self_outings():
    import pathlib
    src = pathlib.Path("mcp_outings.py").read_text(encoding="utf-8")
    assert "if not wake and not garden_wake.games_on(actor):" in src


def test_regenerate_only_removes_the_one_message():
    import pathlib
    src = pathlib.Path("routes/chatroom.py").read_text(encoding="utf-8")
    start = src.index("async def regenerate_chatroom_message")
    block = src[start:src.index("@router", start)]
    assert 'DELETE FROM chatroom_messages WHERE id=?' in block and "created_at>=?\", (room_id, msg_created_at))" not in block
    assert "actor=target[\"sender\"]" in block
