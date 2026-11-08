"""世界窗（11-08 小猫）：[看看:地名] → 天气、时间、公开摄像头画面，AI 接着说；旅行也带上此刻的画面。"""
import asyncio

import pytest

import capabilities
import context_builder
import mcp_policy
import travel_places
import world_window as ww
from web_search import clean_web_command_text


@pytest.mark.parametrize("text,name", [("[看看:东京]", "东京"), ("［看看：巴黎］", "巴黎"), ("[ 看看 : New York ]", "New York")])
def test_pattern(text, name):
    assert mcp_policy.WORLD_LOOK_CMD_PATTERN.search(text).group(1) == name


def test_command_never_shows_up_in_text():
    assert context_builder.strip_tool_commands("好呀[看看:东京]") == "好呀"
    assert clean_web_command_text("好呀[看看:东京]") == "好呀"


PAGE = """<a href="webcam/japan/prefecture-of-hokkaido/sapporo/sapporo-city.html"></a>
<a href="webcam/japan/kanto/tokyo/shinjuku-kabukicho.html"></a>
<a href="webcam/italia/lazio/roma/fontana-di-trevi.html"></a>"""


@pytest.mark.parametrize("city,expect", [
    ("Tokyo", "webcam/japan/kanto/tokyo/shinjuku-kabukicho.html"),
    ("Rome", "webcam/italia/lazio/roma/fontana-di-trevi.html"),
    ("Osaka", ""),
])
def test_match_camera(city, expect):
    assert ww.match_camera(PAGE, city) == expect


class Reply:
    def __init__(self, text="", data=None):
        self.text, self._data, self.content = text, data, text.encode()

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class Client:
    def __init__(self, country="Japan", city="Tokyo", cam=True):
        self.country, self.city, self.cam, self.urls = country, city, cam, []

    async def get(self, url, **kwargs):
        self.urls.append(url)
        if "nominatim" in url:
            return Reply(data={"address": {"city": self.city, "country": self.country}})
        if url.endswith("/webcam/japan.html"):
            return Reply(PAGE if self.cam else "")
        return Reply('<img src="social1234.jpg">')


def _place():
    return travel_places.Place(name="东京", country="日本", latitude=35.6, longitude=139.7)


def test_camera_photo_downloads_the_preview(monkeypatch):
    got = []

    async def download(client, url):
        got.append(url)
        return "/uploads/travel/live.jpg"
    monkeypatch.setattr(travel_places, "_download", download)
    assert asyncio.run(ww.camera_photo(Client(), _place())) == "/uploads/travel/live.jpg"
    assert got == ["https://cdn.skylinewebcams.com/social1234.jpg"]


def test_no_camera_is_empty_not_an_error():
    assert asyncio.run(ww.camera_photo(Client(cam=False), _place())) == ""


def test_site_failure_is_empty():
    class Broken:
        async def get(self, *a, **k):
            raise RuntimeError("down")
    assert asyncio.run(ww.camera_photo(Broken(), _place())) == ""


def _patch_lookup(monkeypatch, photo="/uploads/travel/live.jpg", place=True):
    calls = []

    class Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    async def geocode(client, name):
        calls.append(name)
        return _place() if place else None

    async def weather(client, p):
        p.weather, p.local_time = "晴 12℃", "21:30"

    async def camera(client, p):
        return photo
    monkeypatch.setattr(travel_places, "_client", lambda: Ctx())
    monkeypatch.setattr(travel_places, "geocode", geocode)
    monkeypatch.setattr(travel_places, "fill_weather", weather)
    monkeypatch.setattr(ww, "camera_photo", camera)
    monkeypatch.setattr(ww, "_cache", {})
    return calls


def test_look_text_and_cache(monkeypatch):
    calls = _patch_lookup(monkeypatch)
    found = asyncio.run(ww.look("东京"))
    assert found.photo.endswith("live.jpg") and "晴 12℃" in found.text and "21:30" in found.text
    asyncio.run(ww.look("东京"))
    assert len(calls) == 1  # 10 分钟内不重复取


def test_look_without_camera_says_so(monkeypatch):
    _patch_lookup(monkeypatch, photo="")
    assert "没有画面" in asyncio.run(ww.look("小地方")).text


def test_unknown_place(monkeypatch):
    _patch_lookup(monkeypatch, place=False)
    assert asyncio.run(ww.look("不存在")) is None


def _go(text, monkeypatch, allowed=True, enabled=True, reply="东京好亮", found=True):
    delivered, notes = [], []

    async def deliver(t, photos):
        delivered.append((t, photos))

    async def notify(t):
        notes.append(t)

    async def fake_look(name):
        return ww.Look("东京，日本", "地点：东京", "/uploads/travel/live.jpg") if found else None

    async def fake_reply(actor, f, said, recent):
        return reply
    monkeypatch.setattr(ww, "look", fake_look)
    monkeypatch.setattr(ww, "_reply", fake_reply)
    monkeypatch.setattr(capabilities, "is_capability_enabled", lambda key: enabled)

    async def run():
        out = ww.process(text, "ai3", allowed=allowed, deliver=deliver, notify=notify)
        await asyncio.gather(*list(ww._tasks))
        return out
    return asyncio.run(run()), delivered, notes


def test_process_strips_and_delivers_with_photo(monkeypatch):
    out, delivered, notes = _go("我想看看[看看:东京]", monkeypatch)
    assert out == "我想看看" and delivered == [("东京好亮", ["/uploads/travel/live.jpg"])] and notes == []


def test_only_command_leaves_a_sentence(monkeypatch):
    out, _, _ = _go("[看看:东京]", monkeypatch)
    assert out == "我看看东京现在什么样。"


@pytest.mark.parametrize("kwargs", [{"allowed": False}, {"enabled": False}])
def test_blocked_only_strips(monkeypatch, kwargs):
    out, delivered, _ = _go("好[看看:东京]", monkeypatch, **kwargs)
    assert out == "好" and delivered == []


def test_unknown_place_tells_her(monkeypatch):
    _, delivered, notes = _go("[看看:不存在]", monkeypatch, found=False)
    assert delivered == [] and "没找到" in notes[0]


def test_empty_model_reply_still_shows_the_facts(monkeypatch):
    _, delivered, _ = _go("[看看:东京]", monkeypatch, reply="")
    assert delivered[0][0] == "地点：东京"


def test_reply_attaches_the_photo_for_vision_and_cleans_actions(monkeypatch):
    import ai_providers
    import memory_hub_jobs
    seen = {}

    async def stream(messages, model):
        seen["last"] = messages[-1]
        yield "真好看[MUSIC:x]"
    monkeypatch.setattr(ai_providers, "stream_ai", stream)
    monkeypatch.setattr(memory_hub_jobs, "_persona_messages", lambda a: [])
    monkeypatch.setattr(ww, "model_for", lambda a: "m")
    out = asyncio.run(ww._reply("ai3", ww.Look("东京", "地点：东京", "/uploads/travel/a.jpg"), "想看", []))
    assert seen["last"]["attachments"] == ["/uploads/travel/a.jpg"] and "[MUSIC" not in out


# ── 旅行 ──

def test_brief_mentions_live_camera():
    place = _place()
    place.has_live_camera = True
    place.photos = [{"path": "/x.jpg", "credit": ww.CREDIT}]
    assert "此刻的公开摄像头" in place.brief()


def test_abilities_offered_to_everyone():
    from pathlib import Path
    src = (Path(__file__).parent / "capabilities.py").read_text(encoding="utf-8")
    assert src.count("world_window.ability_text(user_name)") == 2
    for path in ("routes/chat.py", "routes/chatroom.py"):
        assert "world_window.process(" in (Path(__file__).parent / path).read_text(encoding="utf-8")
