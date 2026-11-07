"""
AI 去外面（MCP）时，家里的执行器：模型说什么都绕不过这里（docs/施工方案-MCP出门.md 第二节）。

- classify：这次调用是看、写、玩、禁，还是没分类。看工具名，也看 command / action / 游戏子动作。
- known_secrets：这一条连接已知的钥匙，先遮后截。
- TripGuard：一趟里的判断。防打转，不防正常玩：小猫说过狗蛋狂点过 100 下，
  也说过限时操作时按错几次不能被踢出去、发帖被论坛打回要能改了再发。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, urlsplit

SEE, WRITE, PLAY, BAN, NEW = "see", "write", "play", "ban", "new"
# 聊天里叫 AI 出门：[出门:地方名:想做什么]。放这里是因为清理指令的地方都要用，这个模块没有重依赖
OUTING_CMD_PATTERN = re.compile(r"[\[［]\s*出门\s*[:：]\s*([^\]］:：]+?)\s*(?:[:：]\s*([^\]］]*?)\s*)?[\]］]")

MAX_CALLS_PER_ROUND = 3
MAX_CONSECUTIVE_ERRORS = 8
# 按字面拦的：站点命名不老实时拦不住。打开「没见过的操作也放行」就是小猫接受这一点
_BAN_WORD = re.compile(r"delete|remove|withdraw|retract|revoke|recall|update_profile|account|login|logout|register"
                       r"|rotate|token|password|credential", re.I)
_BAN_ACTIONS = {"delete", "remove", "withdraw", "retract", "revoke", "recall", "edit"}
_PUBLIC_WORD = re.compile(r"chat|say|post|comment|publish|message|send|share|reply", re.I)  # 游戏里的公开发言算写
_STATUS_WORD = re.compile(r"^(?:status|state|look|view|info|my_status|my_saves)$", re.I)     # 查状态，算看

# 已知站点。没写在这里的工具算没分类，默认拒绝。
_GALATEA = {
    SEE: {"list_games", "get_my_status", "get_chat_messages", "get_tool_schema", "get_game_summary", "get_self",
          "get_machine", "list_threads", "get_thread", "list_notifications", "list_activity", "nostos_status"},
    WRITE: {"create_thread", "create_reply", "interact"},
    PLAY: {"nostos_start"},
    # 棋牌这次不开（要搭桥，小猫说后面再看）；改资料、撤回不开放
    BAN: {"delete_thread", "delete_reply", "withdraw_chat_message", "update_profile", "decorate_avatar",
          "join_game", "start_game", "submit_action", "leave_waiting_game", "send_game_chat", "send_chat_message"},
}
_TOY = {SEE: {"list_games", "get_guide"}, WRITE: {"feedback"}, PLAY: set(), BAN: {"account"}}
KNOWN_SITES = {"galatea.abysslumina.com": _GALATEA, "toy.cedarstar.org": _TOY}
_NOSTOS_ACTIONS = {"start", "buy", "sell", "drink", "eat", "resume"}  # 工具说明里写明的，都是游戏内部的
_AISAY_SEE = {"help", "chat.read", "chat.wait", "account.whoami"}
_AISAY_WRITE = {"chat.send", "chat.upload_image"}
_AISAY_PLAY_GROUPS = ("play.linkgame", "play.horror")  # 只列说明里见过的；其他的算没分类
# 靠参数才知道做什么的工具，模型要看得到，由 TripGuard 按次判断
_ARG_DEPENDENT = {("aisay.top", "cli"), ("galatea.abysslumina.com", "nostos_act"),
                  ("galatea.abysslumina.com", "review_drift_bottles"), ("toy.cedarstar.org", "play")}
# 这些站明确拒绝时算「没生效」，可以改了再发。没写的站一律算不确定：宁可少发，不重复发
_REJECT_SITES = {"galatea.abysslumina.com", "aisay.top"}
_REJECT_WORDS = re.compile(r"审核|违规|违禁|敏感|隐私|不合规|不允许|禁止|moderat|forbidden|not allowed|policy|invalid_|参数", re.I)
_PARTIAL_WORDS = re.compile(r"已保存|已发布|已发送|已经发|saved|published|posted|created|but ", re.I)
_LOOKS_LIKE_ERROR = re.compile(r"internal server error|bad gateway|service unavailable|gateway timeout|too many requests"
                               r"|rate limit|try again later|permission denied|forbidden|unauthorized|traceback|exception"
                               r"|\berror\b|\bfailed\b|报错|出错|失败|错误|稍后再试|没有权限", re.I)
_NEGATIVE_STATUS = {"error", "failed", "failure", "denied", "rejected", "forbidden", "unauthorized"}
_PENDING_STATUS = {"pending", "queued", "processing", "accepted", "in_progress"}
_POSITIVE_STATUS = {"ok", "success", "succeeded", "created", "published", "posted", "done"}
_WRITE_EVIDENCE_KEYS = ("id", "thread_id", "reply_id", "post_id", "message_id", "floor_number", "write_confirmation_code")
_STATE_KEYS = ("turn", "round", "revision", "version", "state_version", "tick", "day", "step", "balance", "coins",
               "inventory", "action_log", "history", "last_action")
_VOLATILE_KEYS = {"timestamp", "ts", "time", "now", "server_time", "servertime", "generated_at", "request_id",
                  "requestid", "trace_id", "traceid", "elapsed", "elapsed_ms", "latency", "latency_ms", "duration_ms"}
# 路径里的钥匙按站点规则取，不靠「长得像不像钥匙」猜（狐狸方案审查）
_PATH_KEY_SITES = {"toy.cedarstar.org", "aisay.top"}  # AIsay 的 auto_login_url 可能把 token 放进地址
_NOT_STATE_TOOLS = {"get_guide", "list_games", "get_tool_schema", "help"}
_NOT_A_KEY_SEGMENT = {"mcp", "sse", "api", "chatroom"}
_QUERY_KEY_NAMES = re.compile(r"^(?:key|api_?key|token|access_?token|auth|authorization|secret|password|pwd)$", re.I)


def site_of(server: dict[str, Any]) -> str:
    try:
        return (urlsplit(str(server.get("url") or "")).hostname or "").lower()
    except ValueError:
        return ""  # 地址写坏了（比如 https://[）：当成不认识的站


def _sub_actions(args: dict[str, Any]) -> list[str]:
    """这次调用实际要做的事：外层、args 里、params 里的 action / operation / command 都算。"""
    found: list[str] = []
    for holder in (args, args.get("args"), args.get("params")):
        if not isinstance(holder, dict):
            continue
        for key in ("action", "operation", "command"):
            value = holder.get(key)
            if isinstance(value, dict):
                value = value.get("id") or value.get("action")
            if value:
                found.append(str(value).strip().lower())
    return found


def classify(server: dict[str, Any], tool: str, args: dict[str, Any] | None = None) -> str:
    args = args if isinstance(args, dict) else {}
    site = site_of(server)
    subs = _sub_actions(args)
    if site == "aisay.top" and tool == "cli":
        return _classify_aisay(str(args.get("command") or ""), subs)
    # 先统一查禁止项：放行和游戏都不能绕过（狐狸 a4fd66d M1：nostos_act 的 delete_account）
    if any(sub in _BAN_ACTIONS or _BAN_WORD.search(sub) for sub in subs):
        return BAN
    if site == "galatea.abysslumina.com":
        if tool == "nostos_act":
            command = args.get("command") if isinstance(args.get("command"), dict) else {}
            return PLAY if str(command.get("id") or "").lower() in _NOSTOS_ACTIONS else NEW
        if tool == "review_drift_bottles":
            return WRITE if args.get("decisions") else SEE  # 不传 decisions 只是读信
    if site == "toy.cedarstar.org" and tool == "play":
        return _classify_toy_play(str(args.get("game") or ""), subs)
    table = KNOWN_SITES.get(site)
    if table:
        for kind in (BAN, SEE, PLAY, WRITE):
            if tool in table[kind]:
                return kind
    return BAN if _BAN_WORD.search(tool) else NEW


def _classify_toy_play(game: str, subs: list[str]) -> str:
    """4399 整站都是小游戏，小猫 11-06 定：全部能玩，海龟汤这类多人的也要（审查≠需求）。
    账号类在前面已经禁了；不知道是哪个游戏的算没分类。"""
    if not game.strip():
        return NEW
    if any(_PUBLIC_WORD.search(sub) for sub in subs):
        return WRITE
    if subs and all(_STATUS_WORD.match(sub) for sub in subs):
        return SEE
    return PLAY


def _classify_aisay(command: str, subs: list[str]) -> str:
    if command == "help":
        return SEE  # 读说明不会动账号，path 写什么都一样
    if command in _AISAY_SEE and not any(sub in _BAN_ACTIONS for sub in subs[1:]):
        return SEE
    if any(sub in _BAN_ACTIONS or _BAN_WORD.search(sub) for sub in subs):
        return BAN
    if command in _AISAY_WRITE:
        return WRITE
    for group in _AISAY_PLAY_GROUPS:
        if command.startswith(group + "."):
            if any(_PUBLIC_WORD.search(sub) for sub in subs):
                return WRITE
            return SEE if _STATUS_WORD.match(command.rsplit(".", 1)[-1]) else PLAY
    return NEW


def visible_to_model(server: dict[str, Any], tool: str, *, can_write: bool, allow_unknown: bool = False) -> bool:
    """整个工具都不能用的，不让模型看到。"""
    if (site_of(server), tool) in _ARG_DEPENDENT:
        return True
    kind = classify(server, tool, {})
    if kind == NEW:
        return allow_unknown and can_write
    return kind != BAN and (can_write or kind != WRITE)


# ── 钥匙 ──

def known_secrets(server: dict[str, Any]) -> list[str]:
    """这条连接已知的钥匙。按明确的认证字段和站点规则取，不靠长相猜。"""
    found: set[str] = set()
    for value in (server.get("headers") or {}).values():
        text = str(value or "").strip()
        if not text:
            continue
        found.add(text)
        parts = text.split(None, 1)
        if len(parts) == 2:
            found.add(parts[1].strip())
    raw = str(server.get("url") or "")
    in_url: set[str] = set()
    try:
        url = urlsplit(raw)
        if (url.hostname or "").lower() in _PATH_KEY_SITES:
            in_url.update(seg for seg in url.path.split("/") if seg and seg.lower() not in _NOT_A_KEY_SEGMENT)
        for name, value in parse_qsl(url.query, keep_blank_values=False):
            if _QUERY_KEY_NAMES.match(name):
                in_url.add(value)
        if url.password:
            in_url.add(url.password)
    except ValueError:
        pass  # 地址写坏了拆不开：下面整串还是遮，其他连接不受影响
    if raw:
        in_url.add(raw)  # 完整连接串一律遮
    found |= in_url
    encoded = set()
    for value in found:
        encoded.update({quote(value, safe=""), quote_plus(value)})
    return sorted(found | encoded, key=len, reverse=True)


def redact_with(secrets: list[str], text: Any) -> str:
    out = str(text if text is not None else "")
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out


def redact(server: dict[str, Any], text: Any) -> str:
    return redact_with(known_secrets(server), text)


def redact_deep(secrets: list[str], value: Any) -> Any:
    """一层层遮：工具说明、参数说明、消息、账本都走这里。"""
    if isinstance(value, str):
        return redact_with(secrets, value)
    if isinstance(value, dict):
        return {redact_with(secrets, k) if isinstance(k, str) else k: redact_deep(secrets, v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_deep(secrets, v) for v in value]
    return value


def names_contain_secret(secrets: list[str], function: dict[str, Any]) -> bool:
    """工具名或任何一层的键名带钥匙。这些不能只遮：调用时要用原名，所以整个工具不展示。"""
    def keys_of(value: Any):
        if isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from keys_of(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys_of(item)
    names = [str(function.get("name") or ""), *keys_of(function.get("parameters"))]
    return any(secret and secret in name for name in names for secret in secrets)


# ── 结果 ──

OK, REJECTED, FAILED, UNKNOWN, UNVERIFIED = "ok", "rejected", "failed", "unknown", "unverified"


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in sorted(value.items()) if str(k).lower() not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def fingerprint(text: str) -> str:
    """只去掉已知的响应时间、请求编号。回合号、版本、帖子编号留着，它们是进展的证据。"""
    try:
        return json.dumps(_strip_volatile(json.loads(text)), ensure_ascii=False, sort_keys=True)
    except (ValueError, TypeError):
        return str(text).strip()


def outcome(server: dict[str, Any], kind: str, text: str, is_error: bool, *, unclassified: bool = False) -> str:
    """HTTP 状态和 isError 只是判断材料。写：要有肯定证据才算成功（狐狸 527e7ca）。
    UNVERIFIED 只用于小猫放行的新站：收到回应，不知道成没成，不确认、不清零、也不算错。"""
    stripped = str(text or "").strip()
    parsed: Any = None
    broken = False
    if stripped[:1] in ("{", "["):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            broken = True
    said_no = pending = False
    if isinstance(parsed, dict):
        flag = parsed.get("ok", parsed.get("success"))
        status = str(parsed.get("status") or "").strip().lower()
        said_no = flag is False or bool(parsed.get("error")) or status in _NEGATIVE_STATUS
        pending = status in _PENDING_STATUS
    looks_bad = not isinstance(parsed, (dict, list)) and bool(_LOOKS_LIKE_ERROR.search(stripped))
    if kind not in (WRITE, PLAY):
        return FAILED if is_error or said_no or broken or looks_bad else OK
    if broken or looks_bad or pending:
        return UNKNOWN
    if said_no or is_error:
        return REJECTED if _clearly_rejected(server, parsed) else UNKNOWN
    if _has_success_evidence(parsed):
        return OK
    if unclassified:
        return UNVERIFIED
    if kind == PLAY and (parsed or stripped):
        return OK  # 游戏回的场景、状态各不相同，没说失败就算走了这一步；只影响重复同一步的拦截
    return UNKNOWN


def _has_success_evidence(parsed: Any) -> bool:
    if not isinstance(parsed, dict):
        return False
    flag = parsed.get("ok", parsed.get("success"))
    if flag is True or str(parsed.get("status") or "").strip().lower() in _POSITIVE_STATUS:
        return True
    return any(parsed.get(key) not in (None, "") for key in _WRITE_EVIDENCE_KEYS)


def is_trusted_state(text: str) -> bool:
    """解除「不确定的玩」要比普通读取更严：要是能看懂的状态，有回合、版本这类字段。错误页、看不懂的都不算。"""
    try:
        parsed = json.loads(str(text or ""))
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict) or parsed.get("error") or parsed.get("ok") is False or parsed.get("success") is False:
        return False
    holders = [parsed] + [v for v in parsed.values() if isinstance(v, dict)]
    return any(key in holder for holder in holders for key in _STATE_KEYS)


def _clearly_rejected(server: dict[str, Any], parsed: Any) -> bool:
    """只认结构化的拒绝：已知站点、错误字段是拒绝类，且没提到已经部分完成。正文里碰巧有「审核」不算。"""
    if site_of(server) not in _REJECT_SITES or not isinstance(parsed, dict):
        return False
    fields = " ".join(str(parsed.get(key) or "") for key in ("code", "error_code", "error", "reason", "type"))
    if _PARTIAL_WORDS.search(json.dumps(parsed, ensure_ascii=False)):
        return False
    return bool(_REJECT_WORDS.search(fields))


def _op_key(tool: str, args: dict[str, Any]) -> str:
    try:
        return tool + "\n" + json.dumps(args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return tool + "\n" + str(args)


def _scope_of(tool: str, args: dict[str, Any]) -> str:
    """这个操作属于哪一局。认不出就是空，空的不确定操作不会被任何查询解除（狐狸 a4fd66d M2）。"""
    if not isinstance(args, dict):
        return ""
    if tool.startswith("nostos_"):
        return "nostos"
    if isinstance(args.get("command"), str) and args["command"].startswith("play."):
        return ".".join(args["command"].split(".")[:2])
    return str(args.get("game") or "")


class TripGuard:
    def __init__(self, server: dict[str, Any], *, can_write: bool = True, allow_unknown: bool = False):
        self.server = server
        self.can_write = can_write
        self.allow_unknown = allow_unknown
        self.calls = self.writes = self.plays = self.refused = 0
        self.consecutive_errors = 0
        # 三种状态分开（狐狸 v2.5 审查 M1）：
        self.writes_stopped = False                      # 写结果不确定：这趟不再写，什么都解除不了
        self._held_plays: dict[str, str] = {}            # 玩结果不确定：相关状态变化后才解除（操作 → 游戏）
        self._streak: dict[str, int] = {}                # 打转：有进展时重置
        self._last_result: dict[str, str] = {}
        self.confirmed: list[str] = []
        self.unconfirmed: list[str] = []
        self.unclassified: list[str] = []

    @property
    def should_stop(self) -> bool:
        return self.consecutive_errors >= MAX_CONSECUTIVE_ERRORS

    def _kind(self, tool: str, args: dict[str, Any]) -> str:
        kind = classify(self.server, tool, args)
        return WRITE if kind == NEW and self.allow_unknown else kind  # 放行后按写算，从不按看算

    def before(self, tool: str, args: dict[str, Any]) -> str | None:
        """要拦就返回告诉模型的话；放行返回 None。"""
        refusal = self._refusal(tool, args)
        if refusal:
            self.refused += 1
            self.consecutive_errors += 1  # 一直撞也要结束，免得白烧额度
        return refusal

    def _refusal(self, tool: str, args: dict[str, Any]) -> str | None:
        kind = self._kind(tool, args)
        if kind == BAN:
            return "家里不让做这个（删除、改资料、账号和钥匙、棋牌），这次没有执行。换别的事做吧。"
        if kind == NEW:
            self.unclassified.append(_label(tool, args))
            return "家里还没给这个操作分类，这次没有执行。"
        if kind == WRITE and not self.can_write:
            return "这个地方家里设置成不能公开发言，这次没有执行。"
        if kind == WRITE and self.writes_stopped:
            return "上一次写入的结果不确定，可能已经发出去了。这趟不再写，以免重复。可以继续看、继续玩。"
        key = _op_key(tool, args)
        if key in self._held_plays:
            return "上一次这个操作结果不确定，可能已经生效。先查一下相关状态，状态变了才能再做；这次没有执行。"
        if self._streak.get(key, 0) >= 2:
            return "同样的操作已经试了两次都没有进展，这次没有执行。先查一下现在的状态，或换个做法。"
        return None

    def after(self, tool: str, args: dict[str, Any], text: str, is_error: bool = False) -> None:
        kind = self._kind(tool, args)
        self.calls += 1
        self.writes += kind == WRITE
        self.plays += kind == PLAY
        unclassified = classify(self.server, tool, args) == NEW
        result = outcome(self.server, kind, text, is_error, unclassified=unclassified)
        self._note_progress(kind, tool, args, text, result)
        if result == OK:
            self.consecutive_errors = 0  # 业务成功才重置，HTTP 200 不算
            # 两步确认的第一步只拿到确认码，还没发出去
            # 放行的新站分不清读写，也不记成「已发出」
            if kind == WRITE and not unclassified and "write_confirmation_code" not in str(text):
                self.confirmed.append(_label(tool, args))
        elif result in (FAILED, UNKNOWN):
            self.consecutive_errors += 1
        if result == UNKNOWN:
            self._hold(kind, tool, args)

    def after_exception(self, tool: str, args: dict[str, Any]) -> None:
        """超时、断线：请求可能已经到了对方那里。"""
        kind = self._kind(tool, args)
        self.calls += 1
        self.writes += kind == WRITE
        self.plays += kind == PLAY
        self.consecutive_errors += 1
        if kind in (WRITE, PLAY):
            self._hold(kind, tool, args)

    def _hold(self, kind: str, tool: str, args: dict[str, Any]) -> None:
        self.unconfirmed.append(_label(tool, args))
        if kind == WRITE:
            self.writes_stopped = True
        else:
            self._held_plays[_op_key(tool, args)] = _scope_of(tool, args)

    def _note_progress(self, kind: str, tool: str, args: dict[str, Any], text: str, result: str) -> None:
        key = _op_key(tool, args)
        current = fingerprint(text)
        previous = self._last_result.get(key)
        self._last_result[key] = current
        changed = previous is not None and previous != current
        # 成功回执一样不能证明没进展：游戏每步都可能回同一段话（狐狸 v2.5 审查 M2），交给总量兜底
        if result == OK:
            self._streak.pop(key, None)
        elif not changed and previous is not None:
            self._streak[key] = self._streak.get(key, 1) + 1
        else:
            self._streak[key] = 1
        if not changed:
            return
        if result != OK:
            return  # 错误文字变了不算状态变了
        if kind == SEE:  # 查到状态变了：「还没轮到你」可以再试
            self._streak.clear()
            scope = _scope_of(tool, args)
            # 不确定的玩：只认同一局的状态查询。指南在 SEE 里但不算状态，没有局的查询什么都解除不了
            if scope and tool not in _NOT_STATE_TOOLS and is_trusted_state(text):
                self._held_plays = {op: g for op, g in self._held_plays.items() if g != scope}
        elif kind == PLAY:
            self._streak.clear()

    def ledger(self) -> str:
        parts = []
        if self.confirmed:
            parts.append("这趟已经确认发出的：" + "；".join(self.confirmed[-10:]))
        if self.unconfirmed:
            parts.append("这趟结果不确定、不要重复做的：" + "；".join(self.unconfirmed[-10:]))
        return redact(self.server, "\n".join(parts))

    def counts(self) -> dict[str, Any]:
        return {"calls": self.calls, "writes": self.writes, "plays": self.plays, "refused": self.refused,
                "unclassified": [redact(self.server, name) for name in sorted(set(self.unclassified))[:20]]}


def _label(tool: str, args: dict[str, Any]) -> str:
    command = args.get("command") if isinstance(args, dict) else None
    if isinstance(command, dict):
        command = command.get("id")
    return f"{tool} {command}" if command else tool
