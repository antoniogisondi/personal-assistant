from __future__ import annotations

from fastapi import APIRouter

from gsoi_assistant.api.deps import ContainerDep, UserDep
from gsoi_assistant.api.schemas import AlertOut, AlertsCheckBody, AlertsOut

router = APIRouter(prefix="/v1/alerts", tags=["alerts"])


@router.post("/check", response_model=AlertsOut)
async def check(body: AlertsCheckBody, container: ContainerDep, user_id: UserDep) -> AlertsOut:
    """New email and soon-starting events since the last check (each is reported once)."""
    result = await container.watcher.check(user_id, body.lead_minutes)
    return AlertsOut(
        alerts=[
            AlertOut(
                kind=a.kind,
                key=a.key,
                title=a.title,
                text=a.text,
                spoken=a.spoken,
                important=a.important,
            )
            for a in result.alerts
        ],
        unavailable=result.unavailable,
    )
