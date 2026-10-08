"""PC control tools (level 1). Everything goes through the ToolExecutor like any other tool."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from gsoi_assistant.connectors.pc.backend import FOLDERS, MEDIA_ACTIONS, PcBackend
from gsoi_assistant.connectors.pc.matching import UnsafeUrlError, check_url, clean_name, resolve
from gsoi_assistant.core.errors import ToolError
from gsoi_assistant.core.types import DataClass, Risk
from gsoi_assistant.tools.base import ToolContext, ToolSpec
from gsoi_assistant.tools.registry import AnyTool


class OpenAppIn(BaseModel):
    name: str = Field(
        min_length=1, max_length=80, description="Name of an installed program, as spoken."
    )


class OpenAppOut(BaseModel):
    launched: str


class ListAppsIn(BaseModel):
    query: str = Field(default="", max_length=80)
    limit: int = Field(default=15, ge=1, le=30)


class ListAppsOut(BaseModel):
    apps: list[str]


class OpenUrlIn(BaseModel):
    url: str = Field(
        max_length=2000, description="An http(s) address to open in the default browser."
    )


class OpenedOut(BaseModel):
    opened: str


class OpenFolderIn(BaseModel):
    folder: Literal["documents", "downloads", "desktop", "pictures", "music", "videos"]


class MediaIn(BaseModel):
    action: Literal["play_pause", "next", "previous", "volume_up", "volume_down", "mute"]
    times: int = Field(
        default=1, ge=1, le=10, description="How many times to press (volume steps)."
    )


class DoneOut(BaseModel):
    done: bool = True


def make_pc_tools(backend: PcBackend) -> list[AnyTool]:
    async def open_app(args: OpenAppIn, ctx: ToolContext) -> OpenAppOut:
        apps = {a.name: a for a in backend.apps()}
        result = resolve(args.name, list(apps))
        if result.chosen is None:
            if result.candidates:
                options = ", ".join(clean_name(c) for c in result.candidates)
                raise ToolError(f"Several programs match: {options}. Ask the user which one.")
            raise ToolError("No installed program matches that name.")
        backend.launch(apps[result.chosen])
        return OpenAppOut(launched=clean_name(result.chosen))

    async def list_apps(args: ListAppsIn, ctx: ToolContext) -> ListAppsOut:
        names = [clean_name(a.name) for a in backend.apps()]
        if args.query:
            from gsoi_assistant.connectors.pc.matching import rank

            names = [m.name for m in rank(args.query, names) if m.score >= 0.45]
        return ListAppsOut(apps=names[: args.limit])

    async def open_url(args: OpenUrlIn, ctx: ToolContext) -> OpenedOut:
        try:
            url = check_url(args.url)
        except UnsafeUrlError as exc:
            raise ToolError(f"Cannot open that address: {exc}.") from exc
        backend.open_url(url)
        return OpenedOut(opened=url)

    async def open_folder(args: OpenFolderIn, ctx: ToolContext) -> OpenedOut:
        backend.open_folder(args.folder)
        return OpenedOut(opened=args.folder)

    async def media(args: MediaIn, ctx: ToolContext) -> DoneOut:
        for _ in range(args.times):
            backend.media(args.action)
        return DoneOut()

    return [
        ToolSpec(
            name="pc.open_app",
            description=(
                "Open an installed program on the user's computer (e.g. 'Chrome', 'Spotify'). "
                "Only programs in the Start menu can be opened."
            ),
            input_model=OpenAppIn,
            handler=open_app,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Open the program “{a.name}”",
        ),
        ToolSpec(
            name="pc.list_apps",
            description="List installed programs, optionally filtered by a name.",
            input_model=ListAppsIn,
            handler=list_apps,
            risk=Risk.READ,
        ),
        ToolSpec(
            name="pc.open_url",
            description="Open an http(s) web address in the user's default browser.",
            input_model=OpenUrlIn,
            handler=open_url,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Open {a.url} in the browser",
        ),
        ToolSpec(
            name="pc.open_folder",
            description=f"Open a standard folder ({', '.join(FOLDERS)}) in the file explorer.",
            input_model=OpenFolderIn,
            handler=open_folder,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Open the {a.folder} folder",
        ),
        ToolSpec(
            name="pc.media",
            description=f"Press a media key: {', '.join(MEDIA_ACTIONS)}.",
            input_model=MediaIn,
            handler=media,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Media: {a.action}" + (f" x{a.times}" if a.times > 1 else ""),
        ),
    ]
