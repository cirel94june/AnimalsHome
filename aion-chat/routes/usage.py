"""用量小本本：页面 + 数据接口；价格和每日提醒线可以改。"""

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

import usage_log
from config import BASE_DIR

router = APIRouter()


@router.get("/usage")
async def usage_page():
    return FileResponse(BASE_DIR / "static" / "usage.html", headers={"Cache-Control": "no-cache"})


def _route_of(model: str) -> tuple[str, str, bool]:
    """模型属于哪条线路（中转站）。不是中转站的（内置模型、哨兵等）归「其他」。"""
    from config import MODELS
    info = MODELS.get(model) or {}
    if info.get("route_id"):
        return info["route_id"], info.get("route_name") or info["route_id"], info.get("cache_ttl", "1h") == "1h"
    return usage_log.OTHER_ROUTE, "其他（内置模型、哨兵等）", False


def _all_routes() -> list[dict]:
    """当前所有线路，价格页按这个列（还没用过的线路也能先填价）。"""
    from config import MODELS
    seen: dict[str, dict] = {}
    for info in MODELS.values():
        if info.get("route_id") and info["route_id"] not in seen:
            seen[info["route_id"]] = {"id": info["route_id"], "name": info.get("route_name") or info["route_id"],
                                      "long_cache": info.get("cache_ttl", "1h") == "1h"}
    return list(seen.values()) + [{"id": usage_log.OTHER_ROUTE, "name": "其他（内置模型、哨兵等）", "long_cache": False}]


@router.get("/api/usage")
async def usage_data(days: int = 1):
    data = usage_log.summary(days)
    # 每个模型是谁在用（AI 名片里选的「TA 自己的模型」）
    users: dict[str, list[str]] = {}
    try:
        from actors import list_actors
        for actor in list_actors(include_disabled=False):
            if actor.get("model"):
                users.setdefault(actor["model"], []).append(actor["name"])
    except Exception:
        pass
    for row in data["by_model"]:
        row["users"] = users.get(row["name"], [])
    data["by_route"] = usage_log.route_costs(data["by_model"], usage_log.load_prices(), _route_of)
    data["cost"] = usage_log.total_cost(data["by_route"])
    return data


@router.get("/api/usage/prices")
async def usage_prices():
    prices = usage_log.load_prices()
    return {"daily_limit": prices["daily_limit"], "routes": [
        {**route, "prices": prices["routes"].get(route["id"]) or usage_log._clean_route_prices({})}
        for route in _all_routes()]}


@router.put("/api/usage/prices")
async def save_usage_prices(data: dict = Body(...)):
    return usage_log.save_prices(data)


@router.get("/api/usage/today")
async def usage_today():
    """首页提醒用：今天大约花了多少、超没超自己设的线。"""
    return usage_log.today_spend(_route_of)
