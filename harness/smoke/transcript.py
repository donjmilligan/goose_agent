#!/usr/bin/env python3
"""Render a `goose run --output-format stream-json` log as a readable transcript.

    transcript.py RECIPE.stream.jsonl
    transcript.py --tools SESSION.json     # count tool calls in a `goose session export --format json`

Streamed text chunks are joined; tool calls and results are one line each.
stdlib only; run with ``python3 -I``.
"""

from __future__ import annotations

import json
import sys


def render(path: str, width: int = 600) -> None:
    buf: list[str] = []

    def flush() -> None:
        if buf:
            print("ASSISTANT:", "".join(buf).strip())
            buf.clear()

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "error":
                flush()
                print("ERROR:", event.get("error"))
                continue
            if kind == "complete":
                flush()
                print("COMPLETE:", {k: v for k, v in event.items() if k != "type"})
                continue
            if kind != "message":
                continue
            message = event["message"]
            for content in message.get("content", []):
                ctype = content.get("type")
                if ctype == "text" and message.get("role") == "assistant":
                    buf.append(content.get("text", ""))
                    continue
                flush()
                if ctype == "text":
                    print("USER:", content.get("text", "")[:width])
                elif ctype == "toolRequest":
                    call = content.get("toolCall", {}).get("value", {})
                    print("CALL:", call.get("name"), json.dumps(call.get("arguments"))[:width])
                elif ctype == "toolResponse":
                    print("RESULT:", json.dumps(content.get("toolResult"))[:width])
    flush()


def count_tools(path: str) -> None:
    with open(path, encoding="utf-8") as fh:
        session = json.load(fh)
    counts: dict[str, int] = {}
    for message in session.get("conversation") or []:
        for content in message.get("content", []):
            if content.get("type") == "toolRequest":
                name = content.get("toolCall", {}).get("value", {}).get("name") or "?"
                counts[name] = counts.get(name, 0) + 1
    for name in sorted(counts):
        print(f"{counts[name]:4d} {name}")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--tools":
        count_tools(sys.argv[2])
    elif len(sys.argv) == 2:
        render(sys.argv[1])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
