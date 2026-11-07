"""全家海龟汤：群聊里一起猜 4399 的海龟汤（docs/施工方案-一起玩.md 第一节）。

- 题和判定都由 4399 的裁判来，家里只传话：程序转发，不调模型，不花额度。
- 只有小猫能开局：「来一局海龟汤」（「来一局清汤」只从清汤里抽）。
- 开局以后，群里谁的一行以「问」开头就转给裁判，以「猜：」开头就当作还原汤底；
  「提示」要提示（这局最多 3 次），「海龟汤结束」收局。
- 汤主的话用系统消息发到群里，AI 都看得到。
- 用游客连接。每次连接 4399 都当新游客，房间关不掉，会被 4399 自己清理；这里只管自己这局什么时候算结束。
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
import unicodedata
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

IDLE_SECONDS = 60 * 60         # 一小时没人问，这局就算散了，之后的「问」不再转发
MAX_HINTS = 3
REMIND_EVERY = 12              # 每问 12 次重发一遍汤面（聊天记录只往回看半小时）
MAX_PER_MESSAGE = 3            # 一条消息里最多转几行，免得一口气刷屏
ASK_MAX, GUESS_MAX = 200, 1000
CALL_TIMEOUT = 25

JUDGMENTS = {"yes": "是", "no": "不是", "unrelated": "无关"}

_START = re.compile(r"(?:来|开|玩)\s*(?:一|1)?\s*(?:局|把|盘|个|碗)?\s*(海龟汤|清汤)|海龟汤\s*开局")
_END = re.compile(r"^(?:海龟汤\s*(?:结束|不玩了|收局)|结束\s*海龟汤|不玩海龟汤了)\s*[。.!！~～]*$")
_HINT = re.compile(r"^(?:提示|(?:要|给|来)\s*(?:个|一个|点|一点)?\s*提示)\s*(?:吧|呗)?\s*[。.!！~～?？]*$")
_GUESS = re.compile(r"^(?:猜|还原|汤底是)\s*[:：]\s*(.+)$")
_ASK_COLON = re.compile(r"^问\s*[:：]\s*(.+)$")
_ASK_SPACE = re.compile(r"^问\s+(.+)$")
_ASK_BARE = re.compile(r"^问(?!一下|问|题|你|个|下|我|过|了|号|候|好|句|路)(.+)$")
_LEAD = re.compile(r"^[\s>*\-·•「『“\"]+")

_state: dict[str, dict[str, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_loaded = False

Post = Callable[[str], Awaitable[Any]]


def _path():
    from config import DATA_DIR
    return DATA_DIR / "turtle_soup.json"


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _state.update({k: v for k, v in data.items() if isinstance(v, dict)})
    except FileNotFoundError:
        pass
    except Exception as error:
        print(f"[TURTLE] 读存档失败，当作没有进行中的局: {type(error).__name__}")


def _save() -> None:
    try:
        path = _path()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as error:
        print(f"[TURTLE] 存档失败: {type(error).__name__}")


_WIDTH = str.maketrans({"：": ":", "　": " ", " ": " "})


def _clean_line(line: str) -> str:
    """全角半角统一（只换冒号和空格，问题本身的标点原样留着），去掉开头的引用号、列表号、加粗星号。"""
    line = (line or "").translate(_WIDTH)
    line = _LEAD.sub("", line).replace("**", "").strip()
    return line


def parse_line(line: str) -> tuple[str, str] | None:
    """一行话是不是海龟汤的操作：('ask'|'guess', 内容) / ('hint'|'end', '')，都不是返回 None。"""
    text = _clean_line(line)
    if not text:
        return None
    if _END.match(text):
        return "end", ""
    if _HINT.match(text):
        return "hint", ""
    match = _GUESS.match(text)
    if match:
        return "guess", match.group(1).strip()
    for pattern in (_ASK_COLON, _ASK_SPACE, _ASK_BARE):
        match = pattern.match(text)
        if match and match.group(1).strip(" :"):
            return "ask", match.group(1).strip()
    return None


def wants_start(text: str) -> str | None:
    """小猫说「来一局海龟汤 / 来一局清汤 / 海龟汤开局」→ 'any' 或 'clear'。"""
    match = _START.search(unicodedata.normalize("NFKC", text or ""))
    if not match:
        return None
    return "clear" if match.group(1) == "清汤" else "any"


def active(room_id: str) -> dict[str, Any] | None:
    _load()
    game = _state.get(room_id)
    if not game:
        return None
    if time.time() - float(game.get("last") or 0) > IDLE_SECONDS:
        _state.pop(room_id, None)
        _save()
        return None
    return game


# ── 和 4399 说话 ──

def _guest_server() -> dict[str, Any] | None:
    """游客连接：toy.cedarstar.org/mcp，不带任何人的账号 token。"""
    import mcp_outings
    for server in mcp_outings._cfg()["servers"]:
        parts = urlsplit(str(server.get("url") or ""))
        if parts.hostname == "toy.cedarstar.org" and parts.path.rstrip("/") == "/mcp" and server.get("enabled", True):
            return server
    return None


async def _play(action: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """返回 (结果, 出错时给人看的话)。"""
    import mcp_outings
    import mcp_policy
    server = _guest_server()
    if not server:
        return None, "4399 还没接上（工具页里要有一个 toy.cedarstar.org/mcp 的游客连接）"
    try:
        async with asyncio.timeout(CALL_TIMEOUT):
            async with mcp_outings._Session(server) as session:
                text, is_error = await session.call_result(
                    "play", {"game": "turtle_soup", "action": action, "params": params})
    except Exception as error:
        print(f"[TURTLE] {action} 失败: {type(error).__name__}")
        return None, "4399 这会儿连不上，等一下再试"
    if is_error:
        return None, mcp_policy.redact(server, text)[:200].replace("【cedartoy】", "")
    try:
        data = json.loads(text)
    except ValueError:
        return None, "4399 的回应看不懂"
    return (data, "") if isinstance(data, dict) else (None, "4399 的回应看不懂")


def _rules() -> str:
    return ("怎么玩：问是不是的问题，那一行用「问」开头（比如「问：他是自己走进去的吗」）；"
            "想还原整个故事，用「猜：」开头；要提示就说「提示」（这局最多 3 次）；"
            "不玩了说「海龟汤结束」。题和对错都由 4399 的裁判判，谁都不知道汤底，别替裁判回答。")


def _surface_text(game: dict[str, Any]) -> str:
    tags = "·".join(t for t in re.split(r"[，,\s]+", str(game.get("tags") or "")) if t)
    return f"《{game.get('title') or '无题'}》" + (f"（{tags}）" if tags else "") + f"\n\n{game.get('surface') or ''}"


async def _start(room_id: str, kind: str, post: Post) -> None:
    params: dict[str, Any] = {}
    if kind == "clear":
        listed, problem = await _play("list_puzzles", {"tag": "清汤", "page_size": 50})
        items = (listed or {}).get("items") or []
        if not items:
            await post(f"🐢 汤主：没找到清汤的题（{problem or '题库里没有'}），说「来一局海龟汤」就从全部题里抽。")
            return
        params["puzzle_id"] = random.choice(items)["id"]
    data, problem = await _play("create_random", params)
    if not data or not data.get("id"):
        await post(f"🐢 汤主：这局没开起来：{problem or '4399 没给房间号'}")
        return
    now = time.time()
    _state[room_id] = {"room": str(data["id"]), "title": data.get("title") or "", "tags": data.get("tags") or "",
                       "surface": data.get("surface") or "", "started": now, "last": now, "asks": 0, "hints": 0}
    _save()
    await post(f"🐢 汤主：新的一碗汤 {_surface_text(_state[room_id])}\n\n{_rules()}")


async def _ask(game: dict[str, Any], who: str, question: str, post: Post) -> None:
    question = question[:ASK_MAX]
    data, problem = await _play("ask", {"room_id": game["room"], "content": question})
    if not data:
        await post(f"🐢 汤主：{who}这个问题没问出去：{problem}")
        return
    game["asks"] = int(game.get("asks") or 0) + 1
    judgment = str(data.get("judgment") or "")
    answer = JUDGMENTS.get(judgment, f"（裁判说：{judgment or '没回答'}）")
    text = f"🐢 汤主：{who}问「{question}」→ {answer}"
    if data.get("hint_text"):
        text += f"\n💡 {data['hint_text']}"
    if game["asks"] % REMIND_EVERY == 0:
        text += f"\n\n（已经问了 {game['asks']} 个。再贴一遍汤面：{_surface_text(game)}）"
    await post(text)


async def _guess(room_id: str, game: dict[str, Any], who: str, story: str, post: Post) -> None:
    data, problem = await _play("guess", {"room_id": game["room"], "content": story[:GUESS_MAX]})
    if not data:
        await post(f"🐢 汤主：{who}的还原没交上去：{problem}")
        return
    score = data.get("score")
    score_text = f"还原度 {score}%" if isinstance(score, (int, float)) else ""
    if data.get("correct"):
        answer = data.get("answer") or (data.get("room") or {}).get("answer") or ""
        await post(f"🐢 汤主：🎉 {who}猜中了！{score_text}" + (f"\n\n汤底：{answer}" if answer else "") + "\n\n这局结束。")
        _state.pop(room_id, None)
        return
    await post(f"🐢 汤主：{who}的还原 → 还没对上。{score_text}")


async def _hint(game: dict[str, Any], who: str, post: Post) -> None:
    if int(game.get("hints") or 0) >= MAX_HINTS:
        await post(f"🐢 汤主：这局的 {MAX_HINTS} 次提示已经用完了。")
        return
    data, problem = await _play("hint_request", {"room_id": game["room"]})
    hint = (data or {}).get("hint_text") or (data or {}).get("hint") or (data or {}).get("content")
    if not hint:
        await post(f"🐢 汤主：没拿到提示：{problem or '裁判没给'}")
        return
    game["hints"] = int(game.get("hints") or 0) + 1
    await post(f"🐢 汤主：💡 提示（{who}要的，第 {game['hints']}/{MAX_HINTS} 次）：{hint}")


async def _end(room_id: str, game: dict[str, Any], post: Post) -> None:
    data, _ = await _play("reveal_answer", {"room_id": game["room"]})
    answer = (data or {}).get("answer") or ((data or {}).get("room") or {}).get("answer") or ""
    _state.pop(room_id, None)
    if answer:
        await post(f"🐢 汤主：这局结束。汤底：{answer}")
    else:
        await post(f"🐢 汤主：这局结束。一共问了 {game.get('asks') or 0} 个问题。"
                   "4399 要问满 50 个才给看汤底，所以这次看不到了。")


async def on_message(room_id: str, sender: str, who: str, text: str, post: Post, *, can_start: bool = False) -> bool:
    """群里每条话（小猫的、AI 的）都过一遍。返回 True 表示有海龟汤的动作。

    can_start：只有小猫在群聊房间里的话能开局。
    """
    if not text:
        return False
    lock = _locks.setdefault(room_id, asyncio.Lock())
    async with lock:
        game = active(room_id)
        if not game:
            kind = wants_start(text) if can_start else None
            if kind:
                await _start(room_id, kind, post)
                return True
            return False
        if can_start and wants_start(text):
            await post(f"🐢 汤主：这一局《{game.get('title')}》还没结束，说「海龟汤结束」再开新的。")
            return True
        done = 0
        for line in text.splitlines():
            if done >= MAX_PER_MESSAGE or room_id not in _state:
                break
            parsed = parse_line(line)
            if not parsed:
                continue
            kind, content = parsed
            if kind == "end" and sender != "user":  # 只有小猫能收局
                continue
            done += 1
            game["last"] = time.time()
            if kind == "ask":
                await _ask(game, who, content, post)
            elif kind == "guess":
                await _guess(room_id, game, who, content, post)
            elif kind == "hint":
                await _hint(game, who, post)
            elif kind == "end":
                await _end(room_id, game, post)
        if done:
            _save()
        return bool(done)
