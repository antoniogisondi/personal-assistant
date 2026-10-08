from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from gsoi_assistant.core.types import DataClass, Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool


class NoArgs(BaseModel):
    pass


class NowOut(BaseModel):
    iso: str
    weekday: str
    timezone: str


async def _now(_: NoArgs, ctx: ToolContext) -> NowOut:
    try:
        tz = ZoneInfo(ctx.services.timezone)
    except ZoneInfoNotFoundError:
        tz = UTC  # type: ignore[assignment]
    now = datetime.now(tz)
    return NowOut(
        iso=now.isoformat(timespec="seconds"), weekday=now.strftime("%A"), timezone=str(tz)
    )


time_now = ToolSpec(
    name="time.now",
    description="Get the current date, time and weekday in the user's time zone.",
    input_model=NoArgs,
    handler=_now,
    risk=Risk.READ,
    output_data_class=DataClass.PUBLIC,
)

TIME_TOOLS: list[AnyTool] = [time_now]
