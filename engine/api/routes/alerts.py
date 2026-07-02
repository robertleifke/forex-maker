"""Alert routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from engine.api.deps import get_repository
from engine.types import Alert
from engine.db.repository import DatabaseRepository

router = APIRouter()


@router.get("/alerts", response_model=list[Alert])
async def get_alerts(
    limit: int = Query(20, le=100),
    db: DatabaseRepository = Depends(get_repository),
) -> list[Alert]:
    return await db.alerts.get_alerts(limit)
