"""聊天里的 [TAOBAO:词]（11-06 小猫：前端对话里不能直接去淘宝）。"""
import asyncio

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
