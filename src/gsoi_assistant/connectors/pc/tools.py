"""PC control tools (level 1). Everything goes through the ToolExecutor like any other tool."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator

from gsoi_assistant.connectors.google.gmail import _check_addresses
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
    browser: Literal["chrome", "edge", "firefox", "brave", "opera"] | None = Field(
        default=None,
        description="Open it in this browser instead of the default one (e.g. 'Chrome').",
    )


class OpenedOut(BaseModel):
    opened: str


class ComposeIn(BaseModel):
    to: list[str] = Field(min_length=1, max_length=5)
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=1200)

    @field_validator("to")
    @classmethod
    def _addresses(cls, v: list[str]) -> list[str]:
        return _check_addresses(v)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, v: str) -> str:
        if "\n" in v or "\r" in v:
            raise ValueError("subject must be a single line")
        return v


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
        backend.open_url(url, args.browser)
        return OpenedOut(opened=url)

    async def compose_email(args: ComposeIn, ctx: ToolContext) -> OpenedOut:
        query = "&".join(
            f"{k}={quote(v, safe='')}"
            for k, v in (("subject", args.subject), ("body", args.body))
            if v
        )
        mailto = (
            "mailto:"
            + ",".join(quote(a, safe="@") for a in args.to)
            + (f"?{query}" if query else "")
        )
        if len(mailto) > 1800:
            raise ToolError("The text is too long for a draft window: shorten it or send directly.")
        backend.compose_mail(mailto)
        return OpenedOut(opened="draft in the default mail program")

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
            description=(
                "Open an http(s) web address in the default browser, or in a chosen browser "
                "(chrome, edge, firefox...). Use this for 'open Chrome and go to Instagram': "
                "browser='chrome', url='https://www.instagram.com'. For a web search use "
                "https://www.google.com/search?q=<terms>."
            ),
            input_model=OpenUrlIn,
            handler=open_url,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Open {a.url} in {a.browser or 'the browser'}",
        ),
        ToolSpec(
            name="pc.compose_email",
            description=(
                "Open a new-message window in the user's default mail program (e.g. Outlook), "
                "already filled in. The user reviews it and presses Send themselves: nothing is "
                "sent by this tool. Use it when the user wants to write from their mail program."
            ),
            input_model=ComposeIn,
            handler=compose_email,
            risk=Risk.WRITE_LOCAL,
            output_data_class=DataClass.PUBLIC,
            summarize=lambda a: f"Open a draft to {', '.join(a.to)} in the mail program",
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
