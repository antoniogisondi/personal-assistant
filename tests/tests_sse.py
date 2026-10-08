import json


def parse_sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in (
        text.strip().split("\r\n\r\n") if "\r\n\r\n" in text else text.strip().split("\n\n")
    ):
        ev = data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].strip()
        if ev:
            out.append((ev, json.loads(data)))
    return out
