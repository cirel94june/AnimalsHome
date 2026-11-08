"""替 AI 开户（11-08 小猫）：家里注册，钥匙直接进 TA 的连接；AI 自己仍然不能注册。"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import account_opening as ao
import mcp_outings
import mcp_policy
from routes import mcp_tools
from test_mcp_outings import env  # noqa: F401

TOKEN = "TOKEN_CANARY_0123456789abcdefghij"
PASSWORD = "PASSWORD_CANARY"


@pytest.fixture
def site(env, monkeypatch):  # noqa: F811
    mcp_outings.save_tool({"name": "4399", "url": "https://toy.cedarstar.org/mcp"})
    calls, answer = [], {"text": json.dumps({"token": TOKEN, "user": {"username": "小克"}}), "error": False}

    class Session:
        def __init__(self, server):
            self.server = server

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def call_result(self, name, args):
            calls.append((self.server["url"], name, args))
            if isinstance(answer.get("raise"), Exception):
                raise answer["raise"]
            return answer["text"], answer["error"]
    monkeypatch.setattr(mcp_outings, "_Session", Session)
    app = FastAPI()
    app.include_router(mcp_tools.router)
    return TestClient(app), calls, answer


def _open(client, actor="ai3", username="狗蛋的号", password=PASSWORD):
    return client.post("/api/account-opening/4399", json={"actor": actor, "username": username, "password": password})


def test_opens_and_saves_only_for_that_ai(site):
    client, calls, _ = site
    response = _open(client)
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text and PASSWORD not in response.text
    assert calls == [("https://toy.cedarstar.org/mcp", "account",
                      {"action": "login_or_register", "username": "狗蛋的号", "password": PASSWORD})]
    saved = mcp_outings._server(response.json()["connection"])
    assert saved["url"] == f"https://toy.cedarstar.org/{TOKEN}"
    assert saved["actors"] == ["ai3"] and saved["autonomy"] is False


def test_second_opening_is_refused(site):
    client, calls, _ = site
    assert _open(client).status_code == 200
    again = _open(client)
    assert again.status_code == 400 and "已经有" in again.json()["detail"] and len(calls) == 1


@pytest.mark.parametrize("body,word", [({"username": "a"}, "用户名"), ({"password": "123"}, "密码"),
                                       ({"actor": "ai9"}, "没有这位")])
def test_bad_input_never_reaches_the_site(site, body, word):
    client, calls, _ = site
    response = _open(client, **body)
    assert response.status_code == 400 and word in response.json()["detail"] and calls == []


def test_name_conflict_is_checked_before_registering(site):
    client, calls, _ = site
    mcp_outings.save_tool({"name": ao._connection_name("ai3"), "url": "https://other.example/mcp"})
    assert _open(client).status_code == 400 and calls == []


def test_errors_never_echo_the_password(site):
    client, _, answer = site
    answer.update(text=f"用户名已存在 {PASSWORD}", error=True)
    response = _open(client)
    assert response.status_code == 400 and PASSWORD not in response.text and "用户名已存在" in response.text


@pytest.mark.parametrize("text", ['{"message":"用户名已存在"}', "不是 JSON", '{"token":"短"}'])
def test_no_token_means_nothing_saved(site, text):
    client, _, answer = site
    answer["text"] = text
    assert _open(client).status_code == 400 and not ao.has_account("4399", "ai3")


def test_connection_failure_is_clean(site):
    client, _, answer = site
    answer["raise"] = RuntimeError(PASSWORD)
    response = _open(client)
    assert response.status_code == 400 and PASSWORD not in response.text


def test_unknown_site(site):
    client, _, _ = site
    assert client.post("/api/account-opening/nope", json={}).status_code == 404


def test_overview_shows_who_has_an_account_without_secrets(site):
    client, _, _ = site
    _open(client)
    response = client.get("/api/account-opening")
    assert TOKEN not in response.text
    assert {a["id"]: a["has"]["4399"] for a in response.json()["actors"]}["ai3"] is True


def test_ai_still_cannot_register():
    server = {"url": "https://toy.cedarstar.org/mcp"}
    assert mcp_policy.classify(server, "account", {"action": "login_or_register"}) == mcp_policy.BAN
    assert mcp_policy.classify(server, "play", {"game": "turtle_soup", "action": "register"}) == mcp_policy.BAN


# ── 狐狸 §205 复审的失败测试（原样收进来）──
from urllib.parse import urlsplit  # noqa: E402

import actors  # noqa: E402


def test_json_encoded_error_does_not_reveal_password(site):
    client, _, answer = site
    password = r'PASS"WORD\123'
    answer.update(text=json.dumps({'error': f'bad input: {password}'}), error=True)
    response = _open(client, password=password)
    assert response.status_code == 400
    echoed_error = json.loads(response.json()['detail'])['error']
    assert password not in echoed_error, f'JSON error exposes password: {echoed_error!r}'


def test_unsavable_connection_name_is_rejected_before_registration(site, monkeypatch):
    client, calls, _ = site
    monkeypatch.setattr(actors, 'display_name', lambda actor: '狗蛋🐶')
    response = _open(client)
    assert response.status_code == 400
    assert calls == [], 'registered remotely although the generated connection name cannot be saved'


@pytest.mark.parametrize('url,headers', [
    ('http://toy.cedarstar.org/mcp', ''),
    ('https://toy.cedarstar.org/mcp', 'Authorization: Bearer OTHER_AI_TOKEN_CANARY'),
])
def test_registration_only_uses_clean_https_guest_connection(site, monkeypatch, url, headers):
    client, calls, _ = site
    mcp_outings.save_tool({'name': '4399', 'url': url, 'headers': headers, 'actors': ['aion']},
                          original_name='4399')
    selected = []
    OriginalSession = mcp_outings._Session

    class Observe(OriginalSession):
        def __init__(self, server):
            selected.append(server)
            super().__init__(server)

    monkeypatch.setattr(mcp_outings, '_Session', Observe)
    response = _open(client, actor='ai3')
    if calls:
        server = selected[0]
        assert urlsplit(server['url']).scheme == 'https', 'password was handed to an HTTP connection'
        assert not any(k.lower() == 'authorization' for k in server.get('headers', {})), \
            'registration used another AI authentication header'
