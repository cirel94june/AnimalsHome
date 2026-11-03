"""淘宝联盟（淘宝客）官方接口：在服务器上按关键词搜商品。

11-03 小猫：原作者的淘宝工具写死了他电脑上的路径，服务器上用不了；她申请了淘宝联盟的 AppKey，
拿到「物料搜索」权限（16516，taobao.tbk.dg.material.optional），新版「商品物料获取」（27939，
taobao.tbk.dg.material.optional.upgrade）还在审核。这里两个都支持：先用新版，没权限就退回旧版。

只做搜索（只读）：不加购、不下单、不碰她的淘宝账号。
AppSecret 单独存在 data/taobao_union_secret.json（权限 600），不进设置、不回给页面、不写日志。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx

from config import DATA_DIR

ROUTER_URL = "https://eco.taobao.com/router/rest"
SECRET_PATH = DATA_DIR / "taobao_union_secret.json"
METHOD_UPGRADE = "taobao.tbk.dg.material.optional.upgrade"  # 27939（新版）
METHOD_LEGACY = "taobao.tbk.dg.material.optional"            # 16516（旧版）
_APP_KEY = re.compile(r"[0-9A-Za-z]{4,40}")
_ADZONE = re.compile(r"[0-9]{4,20}")
_ITEM_ID = re.compile(r"[0-9A-Za-z_-]{5,80}")  # 淘宝客现在给的多是加密商品 ID（带字母和横线）


class UnionError(RuntimeError):
    """淘宝联盟接口报错（说明里不含钥匙）。"""

    def __init__(self, message: str, *, permission: bool = False):
        super().__init__(message)
        self.permission = permission


def valid_app_key(value: str) -> bool:
    return bool(_APP_KEY.fullmatch(str(value or "")))


def valid_adzone_id(value: str) -> bool:
    return bool(_ADZONE.fullmatch(str(value or "")))


def has_secret() -> bool:
    return bool(_secret())


def _secret() -> str:
    try:
        return str(json.loads(SECRET_PATH.read_text(encoding="utf-8")).get("app_secret") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def save_secret(secret: str) -> None:
    secret = str(secret or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z]{8,80}", secret):
        raise ValueError("AppSecret 格式不对（应是一串字母和数字）")
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SECRET_PATH.with_suffix(".tmp")
    # 先建成只有本程序能读的文件再写内容，替换过去
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"app_secret": secret}, fh)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, SECRET_PATH)


def sign(params: dict[str, str], secret: str) -> str:
    """淘宝开放平台 MD5 签名：secret + 按参数名排序的 key+value + secret，取大写十六进制。"""
    body = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hashlib.md5(f"{secret}{body}{secret}".encode("utf-8")).hexdigest().upper()


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


async def call(method: str, biz: dict, *, app_key: str, timeout: float = 20.0) -> dict:
    secret = _secret()
    if not secret:
        raise UnionError("还没填淘宝联盟的 AppSecret")
    params = {
        "method": method, "app_key": app_key, "format": "json", "v": "2.0", "sign_method": "md5",
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        **{k: str(v) for k, v in biz.items() if v not in (None, "")},
    }
    params["sign"] = sign(params, secret)
    try:
        async with _client(timeout) as client:
            response = await client.post(ROUTER_URL, data=params)
    except httpx.HTTPError as exc:
        raise UnionError(f"连不上淘宝联盟接口（{type(exc).__name__}）") from None
    if response.status_code != 200:
        raise UnionError(f"淘宝联盟接口返回 HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise UnionError("淘宝联盟接口返回的不是 JSON") from None
    error = data.get("error_response") if isinstance(data, dict) else None
    if error:
        code = str(error.get("code") or "")
        sub = str(error.get("sub_code") or "")
        text = "；".join(str(x) for x in (error.get("msg"), error.get("sub_msg")) if x)
        permission = code == "11" or "permission" in (text + sub).lower()
        raise UnionError(f"淘宝联盟接口报错 {code}：{text}"[:300], permission=permission)
    return data


def _https(url: str) -> str:
    url = str(url or "").strip()
    return "https:" + url if url.startswith("//") else url


def _taobao_link(url: str) -> str:
    url = _https(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or not (
            host in ("taobao.com", "tmall.com") or host.endswith(".taobao.com") or host.endswith(".tmall.com")):
        return ""
    return url[:16000]


def _image(url: str) -> str:
    url = _https(url)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == "alicdn.com" or host.endswith(".alicdn.com")):
        return url[:2500]
    return ""


def normalize(raw: dict) -> dict | None:
    """把新旧两版接口的一件商品统一成心愿角用的样子；缺商品 ID、标题或淘宝链接的不要。"""
    if not isinstance(raw, dict):
        return None
    basic = raw.get("item_basic_info") if isinstance(raw.get("item_basic_info"), dict) else {}
    price = raw.get("price_promotion_info") if isinstance(raw.get("price_promotion_info"), dict) else {}
    publish = raw.get("publish_info") if isinstance(raw.get("publish_info"), dict) else {}
    item_id = str(raw.get("item_id") or raw.get("num_iid") or "").strip()
    title = str(basic.get("title") or raw.get("title") or "").strip()[:500]
    if not _ITEM_ID.fullmatch(item_id) or not title:
        return None
    # 链接：能拿到原始商品页就用它（核对 id），否则用联盟给的推广链接
    url = ""
    item_url = _taobao_link(raw.get("item_url") or "")
    if item_url and parse_qs(urlsplit(item_url).query).get("id") == [item_id]:
        url = item_url
    for candidate in (publish.get("click_url"), raw.get("url"), raw.get("click_url"),
                      publish.get("coupon_share_url"), raw.get("coupon_share_url")):
        if url:
            break
        url = _taobao_link(candidate or "")
    if not url:
        return None
    shown_price = (price.get("final_promotion_price") or price.get("zk_final_price")
                   or raw.get("zk_final_price") or raw.get("reserve_price") or "")
    return {
        "item_id": item_id, "title": title, "url": url,
        "image": _image(basic.get("pict_url") or raw.get("pict_url") or ""),
        "shop": str(basic.get("shop_title") or raw.get("shop_title") or "").strip()[:300],
        "price": str(shown_price).strip()[:80], "sku_id": "",
    }


def _map_data(data: dict) -> list:
    for key, value in data.items():
        if key.endswith("_response") and isinstance(value, dict):
            result = value.get("result_list") or {}
            items = result.get("map_data") if isinstance(result, dict) else result
            return items if isinstance(items, list) else []
    return []


async def search(keyword: str, *, app_key: str, adzone_id: str, page_size: int = 20) -> dict:
    """返回 {"products": [...已校验...], "skipped": 不合格的件数, "method": 用的哪个接口}。"""
    if not valid_app_key(app_key) or not valid_adzone_id(adzone_id):
        raise UnionError("淘宝联盟的 AppKey 或推广位 ID 还没填好")
    biz = {"q": keyword, "adzone_id": adzone_id, "page_size": page_size, "page_no": 1}
    try:
        data, method = await call(METHOD_UPGRADE, biz, app_key=app_key), METHOD_UPGRADE
    except UnionError as exc:
        if not exc.permission:
            raise
        # 新版还没批下来：用已经批了的旧版「物料搜索」
        data, method = await call(METHOD_LEGACY, biz, app_key=app_key), METHOD_LEGACY
    raw_items = _map_data(data)
    products = [p for p in (normalize(r) for r in raw_items) if p]
    return {"products": products, "skipped": len(raw_items) - len(products), "method": method}
