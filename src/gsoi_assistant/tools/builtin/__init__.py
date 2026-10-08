from gsoi_assistant.tools.builtin.notes import NOTE_TOOLS
from gsoi_assistant.tools.builtin.tasks import TASK_TOOLS
from gsoi_assistant.tools.builtin.timetools import TIME_TOOLS
from gsoi_assistant.tools.registry import AnyTool

BUILTIN_TOOLS: list[AnyTool] = [*TIME_TOOLS, *NOTE_TOOLS, *TASK_TOOLS]

__all__ = ["BUILTIN_TOOLS"]
