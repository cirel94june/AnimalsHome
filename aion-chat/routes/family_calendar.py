"""小日历的页面和接口。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import family_calendar as cal
from config import BASE_DIR

router = APIRouter()


@router.get("/calendar")
async def calendar_page():
    return FileResponse(BASE_DIR / "static" / "calendar.html", headers={"Cache-Control": "no-cache"})


@router.get("/api/calendar/month")
async def month(year: int, month: int):
    try:
        return await cal.month(year, month)
    except cal.CalendarError as error:
        raise HTTPException(400, str(error))


@router.get("/api/calendar/day")
async def day(date: str):
    try:
        return await cal.day_detail(date)
    except cal.CalendarError as error:
        raise HTTPException(400, str(error))


@router.post("/api/calendar/mood")
async def mood(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "格式不正确")
    if not isinstance(body, dict):
        raise HTTPException(400, "格式不正确")
    try:
        return cal.set_mood(str(body.get("date") or ""), str(body.get("mood") or ""), str(body.get("note") or ""))
    except cal.CalendarError as error:
        raise HTTPException(400, str(error))
