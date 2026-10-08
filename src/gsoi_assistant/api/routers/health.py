from __future__ import annotations

from fastapi import APIRouter, HTTPException

from gsoi_assistant.api.deps import ContainerDep
from gsoi_assistant.api.schemas import HealthBody

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthBody)
async def health() -> HealthBody:
    return HealthBody(status="ok")


@router.get("/ready", response_model=HealthBody)
async def ready(container: ContainerDep) -> HealthBody:
    try:
        await container.repo.ping()
    except Exception as exc:
        raise HTTPException(503, "database unavailable") from exc
    return HealthBody(status="ready")
