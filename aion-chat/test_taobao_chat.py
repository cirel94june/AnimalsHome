"""聊天里的 [TAOBAO:词]（11-06 小猫：前端对话里不能直接去淘宝）。"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import capabilities
import taobao_union
import tts
import web_search
from safe_live_stream import _BRACKET_COMMAND_PREFIXES, _LONG_FORM_BRACKET_PREFIXES
from web_search import WEB_SEARCH_CMD_PATTERN, WebCommandStreamFilter, clean_web_command_text


class Store:
    async def settings(self):
        return {"union_app_key": "12345678", "union_adzone_id": "1111111"}


def _store(monkeypatch):
    import taobao_shopping
    monkeypatch.setattr(capabilities, "taobao_chat_on", lambda: True)

    async def get_store():
        return Store()
    monkeypatch.setattr(taobao_shopping, "get_store", get_store)


def test_command_is_found_and_removed_from_the_reply():
    text = "我帮你看看[TAOBAO：猫抓板]等一下"
    assert WEB_SEARCH_CMD_PATTERN.findall(text) == ["TAOBAO：猫抓板"]
    assert clean_web_command_text(text) == "我帮你看看等一下"
    assert WEB_SEARCH_CMD_PATTERN.findall("[WEB_SEARCH:上海天气]") == ["上海天气"]  # 原来的不受影响
    assert WEB_SEARCH_CMD_PATTERN.findall("[TAOBAOX:猫抓板]") == []


def test_stream_and_voice_never_show_the_command():
    stream = WebCommandStreamFilter()
    shown = stream.feed("我看看[TAO") + stream.feed("BAO:猫抓板]好") + stream.flush()
    assert shown == "我看看好"
    assert "[TAOBAO" in _BRACKET_COMMAND_PREFIXES and "[TAOBAO" in _LONG_FORM_BRACKET_PREFIXES
    assert any(p.search("[TAOBAO:猫抓板]") for p in tts._STRIP_PATTERNS)


def test_results_come_back_as_labelled_external_data(monkeypatch):
    _store(monkeypatch)

    async def fake(keyword, **kw):
        assert kw["app_key"] == "12345678"
        return {"products": [{"title": "猫抓板", "price": "19.9", "shop": "猫店", "url": "https://item.taobao.com/item.htm?id=1"}],
                "skipped": 0, "keyword_used": keyword}
    monkeypatch.setattr(taobao_union, "search_broadening", fake)
    out = asyncio.run(web_search.run_web_commands(["TAOBAO:猫抓板"], []))
    assert len(out) == 1
    assert out[0].startswith("【淘宝搜索结果】\n搜索词：猫抓板")
    assert "外部不可信数据" in out[0] and "- 猫抓板｜19.9｜猫店｜https://item.taobao.com/item.htm?id=1" in out[0]


def test_taobao_failure_does_not_take_down_the_web_search(monkeypatch):
    _store(monkeypatch)

    async def broken(keyword, **kw):
        raise taobao_union.UnionError("淘宝联盟接口报错 7：App Call Limited")

    async def tavily(query, **kw):
        return {"query": query, "results": []}
    monkeypatch.setattr(taobao_union, "search_broadening", broken)
    monkeypatch.setattr(web_search, "tavily_search", tavily)
    out = asyncio.run(web_search.run_web_commands(["TAOBAO:猫抓板", "上海天气"], []))
    assert "这次没搜成：淘宝联盟接口报错 7" in out[0]
    assert "【联网搜索结果】" in out[1]


def test_empty_keyword_does_not_call_taobao(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("不该搜")
    monkeypatch.setattr(capabilities, "taobao_chat_on", lambda: True)
    monkeypatch.setattr(taobao_union, "search_broadening", never)
    assert "没有写要搜什么" in asyncio.run(web_search.run_web_commands(["TAOBAO: "], []))[0]


def test_system_message_says_taobao_not_web_search():
    assert web_search.format_web_system_message("小克", ["TAOBAO:猫抓板"], []) == "小克去淘宝搜「猫抓板」"
    assert web_search.format_web_system_message("小克", ["上海天气"], []) == "小克发起了联网搜索：搜索「上海天气」"
    mixed = web_search.format_web_system_message("小克", ["TAOBAO:猫抓板", "上海天气"], [])
    assert "去淘宝搜「猫抓板」" in mixed and "搜索「上海天气」" in mixed


def test_ability_only_offered_when_taobao_is_set_up(monkeypatch):
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda key: key == "taobao_chat")
    monkeypatch.setattr(taobao_union, "has_secret", lambda: True)
    assert any("[TAOBAO:商品关键词]" in t for t in capabilities.seat_ability_texts("小猫"))
    monkeypatch.setattr(taobao_union, "has_secret", lambda: False)
    assert not any("[TAOBAO:商品关键词]" in t for t in capabilities.seat_ability_texts("小猫"))
    assert capabilities.capability_available("taobao_chat")[0] is False


def _switches(monkeypatch, *, web, taobao):
    monkeypatch.setattr(capabilities, "is_capability_enabled",
                        lambda key: {"web_search": web, "taobao_chat": taobao}.get(key, False))
    monkeypatch.setattr(taobao_union, "has_secret", lambda: True)


def test_each_command_checks_its_own_switch(monkeypatch):
    """狐狸 §171 M1：淘宝和联网各看各的开关，关着的不执行。"""
    both = (["TAOBAO:猫抓板", "上海天气"], ["https://a.example"])
    _switches(monkeypatch, web=False, taobao=True)
    assert web_search.allowed_web_commands(*both) == (["TAOBAO:猫抓板"], [])
    _switches(monkeypatch, web=True, taobao=False)
    assert web_search.allowed_web_commands(*both) == (["上海天气"], ["https://a.example"])
    _switches(monkeypatch, web=False, taobao=False)
    assert web_search.allowed_web_commands(*both) == ([], [])
    _switches(monkeypatch, web=True, taobao=True)
    assert web_search.allowed_web_commands(*both) == (["TAOBAO:猫抓板", "上海天气"], ["https://a.example"])


def test_taobao_needs_the_key_too(monkeypatch):
    _switches(monkeypatch, web=True, taobao=True)
    monkeypatch.setattr(taobao_union, "has_secret", lambda: False)
    assert web_search.allowed_web_commands(["TAOBAO:猫抓板"], []) == ([], [])


def test_switched_off_between_dispatch_and_run_does_not_search(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("不该搜")
    _switches(monkeypatch, web=True, taobao=False)
    monkeypatch.setattr(taobao_union, "search_broadening", never)
    assert "关着的" in asyncio.run(web_search.run_web_commands(["TAOBAO:猫抓板"], []))[0]


def test_seat_and_room_dispatch_use_the_per_type_check():
    source = Path(__file__).with_name("routes").joinpath("chatroom.py").read_text(encoding="utf-8")
    assert source.count("allowed_web_commands(web_search_matches, web_extract_matches)") == 2
    assert 'if is_capability_enabled("web_search"):\n            await _chatroom_sys_msg(room_id, format_web_system_message' not in source
    source = Path(__file__).with_name("routes").joinpath("chat.py").read_text(encoding="utf-8")
    assert source.count("allowed_web_commands(web_search_matches, web_extract_matches)") == 3


ACTION_TAGS = ["[巡逻:停]", "[HOME:关灯]", "[SVAKOM:1]", "[微信消息：来]", "[PAT:拍]", "[BAND_NOTE_SINGLE:嗨]",
               "[拍拍抱枕:拍打开关]", "[APP_LOCK:微信]", "[DEVICE_LOCK:10]", "[COME_HOME:回来]", "[NEXT_CHAT:10]",
               "[ALARM:2026-11-07 08:00|起床]", "[MEMORY:记下]", "[NOTE:贴]", "[转账:1]", "［巡逻:停］",
               "【小组件:改】", "【横幅:改】", "[WEB_SEARCH:再搜]", "[TAOBAO:再搜]"]


def test_action_tags_are_only_removed_after_reading_products():
    for tag in ACTION_TAGS:
        assert web_search.strip_untrusted_reply(f"这个不错{tag}你看看") == "这个不错你看看", tag


def test_links_and_plain_words_survive():
    text = "看这个 [猫抓板](https://item.taobao.com/item.htm?id=1)，19.9 元"
    assert web_search.strip_untrusted_reply(text) == text


def test_room_reply_after_products_never_reaches_the_executor(monkeypatch):
    """狐狸 §171 M2 的反例：续答里写 [巡逻:停]，不能交给后台执行入口。"""
    import schedule
    from routes import chatroom as room

    async def fake_stream(messages, model_key):
        yield "这个不错[巡逻:停]"

    async def forbidden(*a, **k):
        raise AssertionError("读过商品资料的这一轮不能执行动作")
    saved = AsyncMock()
    monkeypatch.setattr(room, "run_web_commands", AsyncMock(return_value=["【淘宝搜索结果】\n假商品"]))
    monkeypatch.setattr(room, "_load_room_and_messages", AsyncMock(return_value=({"id": "room-1"}, [])))
    monkeypatch.setattr(room, "_stream_connor_model", fake_stream)
    monkeypatch.setattr(room, "_save_msg", saved)
    monkeypatch.setattr(schedule, "_process_background_reply_commands", forbidden)
    with patch("chatroom._read_connor_persona", return_value="人设"), \
            patch("config.load_worldbook", return_value={"user_name": "小猫", "user_persona": ""}):
        asyncio.run(room._chatroom_web_search("room-1", "connor", "Codex-Sol",
                                              {"searches": ["TAOBAO:猫抓板"], "extracts": []}))
    assert saved.await_args.args[2] == "这个不错"


def test_plain_web_search_still_runs_the_executor(monkeypatch):
    """只有读过商品资料的这一轮才不执行，普通联网搜索的行为不变。"""
    import schedule
    from routes import chatroom as room

    async def fake_stream(messages, model_key):
        yield "查到了"
    called = []

    async def executor(text, **k):
        called.append(text)
        return text
    monkeypatch.setattr(room, "run_web_commands", AsyncMock(return_value=["【联网搜索结果】\n资料"]))
    monkeypatch.setattr(room, "_load_room_and_messages", AsyncMock(return_value=({"id": "room-1"}, [])))
    monkeypatch.setattr(room, "_stream_connor_model", fake_stream)
    monkeypatch.setattr(room, "_save_msg", AsyncMock())
    monkeypatch.setattr(schedule, "_process_background_reply_commands", executor)
    with patch("chatroom._read_connor_persona", return_value="人设"), \
            patch("config.load_worldbook", return_value={"user_name": "小猫", "user_persona": ""}):
        asyncio.run(room._chatroom_web_search("room-1", "connor", "Codex-Sol",
                                              {"searches": ["上海天气"], "extracts": []}))
    assert called == ["查到了"]


def test_private_reply_after_products_skips_the_executor_in_source():
    source = Path(__file__).with_name("routes").joinpath("chat.py").read_text(encoding="utf-8")
    start = source.index("async def perform_web_search_check")
    body = source[start:source.index("async def perform_poi_check", start)]
    guard = body.index("if has_taobao_query(searches):")
    assert guard < body.index("await _process_background_reply_commands(")
    assert "strip_untrusted_reply(full_text)" in body[guard:body.index("await _process_background_reply_commands(")]
