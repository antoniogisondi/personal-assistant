from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from gsoi_assistant.tools.base import ToolSpec

AnyTool = ToolSpec[Any, Any]


class ToolRegistry:
    def __init__(self, tools: Iterable[AnyTool] = ()) -> None:
        self._by_name: dict[str, AnyTool] = {}
        self._by_wire: dict[str, AnyTool] = {}
        for t in tools:
            self.register(t)

    def register(self, tool: AnyTool) -> None:
        if tool.name in self._by_name or tool.wire_name in self._by_wire:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._by_name[tool.name] = tool
        self._by_wire[tool.wire_name] = tool

    def get(self, name: str) -> AnyTool | None:
        return self._by_name.get(name)

    def resolve(self, name_from_model: str) -> AnyTool | None:
        """Accept the wire name (what the model uses) or the canonical dotted name."""
        return self._by_wire.get(name_from_model) or self._by_name.get(name_from_model)

    def all(self, domains: set[str] | None = None) -> list[AnyTool]:
        tools = sorted(self._by_name.values(), key=lambda t: t.name)
        return [t for t in tools if domains is None or t.domain in domains]

    def __len__(self) -> int:
        return len(self._by_name)
