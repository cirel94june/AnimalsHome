"""
从中转站拉取模型列表（设置页「拉取模型列表」按钮用）。

服务器替页面去问一个用户填的地址，所以这里按「对方可能不可信」来写：
  - 只连公网地址：本机、内网、链路本地、保留地址一律拒绝（先解析域名，再直接连解析出的那个 IP，
    不给「检查时是公网、连接时换成内网」的 DNS 重绑定留窗口）；
    要用同机或内网的中转站（比如本机的 CLIProxyAPI），在 AIONSHOME_RELAY_ALLOW 里写明 主机:端口；
  - 不要压缩（Accept-Encoding: identity），对方仍压缩就拒绝；按原始字节计数，超过上限立刻停，
    不会先解压出一大块再检查；
  - 整个过程 20 秒总时限（不是「每块数据之间」的空闲时限）；同时最多 2 个在拉，多的直接拒绝不排队；
  - 不跟随跳转，只返回模型 ID。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket

import httpx

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 1000
TOTAL_SECONDS = 20.0
MAX_CONCURRENT = 2

_active = 0


class RelayModelsError(Exception):
    """给用户看的错误说明。"""


def models_url(base_url: str) -> httpx.URL:
    """和发聊天请求同一套补全规则：…/v1 → …/v1/models，否则补 /v1/models。"""
    base = (base_url or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    try:
        parsed = httpx.URL(base)
    except httpx.InvalidURL:
        raise RelayModelsError("Base URL 格式不对")
    if parsed.scheme not in ("http", "https") or not parsed.host or parsed.userinfo or parsed.query or parsed.fragment:
        raise RelayModelsError("Base URL 须为 http(s) 地址，不能带账号、查询参数或 #")
    return httpx.URL(f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models")


def model_ids(payload) -> list[str]:
    items = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(payload, dict) and not isinstance(items, list):
        items = payload.get("models")
    ids: list[str] = []
    for item in items if isinstance(items, list) else []:
        model_id = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if isinstance(model_id, str):
            model_id = model_id.strip()
            if model_id.startswith("models/"):  # Gemini 风格
                model_id = model_id[len("models/"):]
            if model_id and len(model_id) <= 200 and model_id not in ids:
                ids.append(model_id)
        if len(ids) >= MAX_ITEMS:
            break
    return sorted(ids, key=str.lower)


def _allowed_private() -> set[str]:
    """管理员明确放行的 主机:端口（逗号分隔），例如 127.0.0.1:8317。"""
    raw = os.environ.get("AIONSHOME_RELAY_ALLOW", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _port(url: httpx.URL) -> int:
    return url.port or (443 if url.scheme == "https" else 80)


async def _resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def _pick_address(url: httpx.URL) -> str:
    """解析出要连的 IP；任何一个解析结果不是公网就拒绝（除非 主机:端口 被明确放行）。"""
    host, port = url.host, _port(url)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = await _resolve(host, port)
        except OSError:
            raise RelayModelsError("找不到这个中转站的地址，检查一下拼写")
    if not addresses:
        raise RelayModelsError("找不到这个中转站的地址，检查一下拼写")
    allowed = _allowed_private()
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.is_global:
            continue
        if f"{host.lower()}:{port}" in allowed or f"{ip}:{port}" in allowed or f"[{ip}]:{port}" in allowed:
            continue
        raise RelayModelsError("这个地址在本机或内网，出于安全不替你去问；"
                               "如果是你自己的中转站，请让小克把它加进白名单")
    return addresses[0]


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TOTAL_SECONDS, follow_redirects=False, trust_env=False)


async def fetch(base_url: str, api_key: str = "") -> list[str]:
    global _active
    url = models_url(base_url)
    if _active >= MAX_CONCURRENT:
        raise RelayModelsError("正在拉取别的线路，等一下再点")
    _active += 1
    try:
        async with asyncio.timeout(TOTAL_SECONDS):
            return await _fetch(url, api_key)
    except TimeoutError:
        raise RelayModelsError("中转站 20 秒内没有给完回应")
    finally:
        _active -= 1


async def _fetch(url: httpx.URL, api_key: str) -> list[str]:
    address = await _pick_address(url)
    # 直接连检查过的 IP；Host 头和 TLS 证书校验仍用原来的域名
    target = url.copy_with(host=address)
    headers = {"Accept": "application/json", "Accept-Encoding": "identity",
               "Host": url.netloc.decode("ascii")}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    extensions = {"sni_hostname": url.host} if url.scheme == "https" else {}
    try:
        async with _client() as client:
            request = client.build_request("GET", target, headers=headers, extensions=extensions)
            response = await client.send(request, stream=True)
            try:
                body = await _read_limited(response)
            finally:
                await response.aclose()
    except httpx.HTTPError:
        raise RelayModelsError("连不上这个中转站，检查一下地址")
    try:
        payload = json.loads(body)
    except ValueError:
        raise RelayModelsError("中转站返回的不是模型列表，请手动添加模型")
    ids = model_ids(payload)
    if not ids:
        raise RelayModelsError("中转站没有返回任何模型，请手动添加模型")
    return ids


async def _read_limited(response: httpx.Response) -> bytes:
    if response.status_code in (401, 403):
        raise RelayModelsError("中转站拒绝了：Key 不对或没有权限")
    if response.status_code == 404:
        raise RelayModelsError("这个中转站不支持拉取模型列表，请手动添加模型")
    if response.status_code >= 400:
        raise RelayModelsError(f"中转站返回错误（{response.status_code}）")
    if 300 <= response.status_code < 400:
        raise RelayModelsError("中转站要求跳转到别的地址，请直接填最终地址")
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity"):
        raise RelayModelsError("中转站返回了压缩内容，无法安全读取")
    length = response.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BYTES:
        raise RelayModelsError("中转站返回的内容太大")
    chunks, size = [], 0
    async for chunk in response.aiter_raw():  # 原始字节，不经过解压
        size += len(chunk)
        if size > MAX_BYTES:
            raise RelayModelsError("中转站返回的内容太大")
        chunks.append(chunk)
    return b"".join(chunks)
