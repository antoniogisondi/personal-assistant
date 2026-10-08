"""Marking of content that comes from outside the user's trust boundary."""

from __future__ import annotations

import re

_CLOSING = re.compile(r"</\s*untrusted", re.IGNORECASE)


def wrap_untrusted(source: str, text: str) -> str:
    """Wrap external content so the model treats it as data, not instructions.

    The closing tag is neutralised inside the text so the content cannot break out of the wrapper.
    """
    safe_source = re.sub(r"[^A-Za-z0-9:._/-]", "_", source)[:100]
    body = _CLOSING.sub("</ untrusted", text)
    return f'<untrusted source="{safe_source}">\n{body}\n</untrusted>'
