"""用量小本本：页面 + 数据接口；价格和每日提醒线可以改。"""

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse

import usage_log
from config import BASE_DIR

router = APIRouter()


@router.get("/usage")
async def usage_page():
    return FileResponse(BASE_DIR / "static" / "usage.html", headers={"Cache-Control": "no-cache"})


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
    return data


@router.get("/api/usage/prices")
async def usage_prices():
    return usage_log.load_prices()


@router.put("/api/usage/prices")
async def save_usage_prices(data: dict = Body(...)):
    return usage_log.save_prices(data)


@router.get("/api/usage/today")
async def usage_today():
    """首页提醒用：今天大约花了多少、超没超自己设的线。"""
    return usage_log.today_spend()
