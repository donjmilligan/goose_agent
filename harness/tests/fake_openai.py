"""A scripted OpenAI-compatible chat endpoint for driving real goose without a model.

goose's ``openai`` provider talks to ``$OPENAI_HOST/v1/chat/completions``. This
server answers that endpoint deterministically, so a live ``goose run`` goes
through the real agent loop (tool calling, PreToolUse, Stop) with no network,
no API key and no weights:

* a title-generation request gets a fixed title;
* a user turn containing ``RUN: <command>`` gets one ``shell`` tool call with
  that command; any other user turn gets a plain text reply;
* the turn after a tool result gets the text ``FAKE_DONE``, which ends the turn
  (so ``Stop`` fires).

Every request body is appended to ``requests.jsonl`` in the log directory, so a
test can read the tool list goose offered.

Run: ``python3 fake_openai.py <log-dir>``; it prints ``PORT <n>`` once listening.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL = "fake-model"
TITLE_MARKER = "Generate a short title"
RUN_MARKER = "RUN: "
DONE_TEXT = "FAKE_DONE"


def _text_of(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def plan_reply(body: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"text": str}`` or ``{"tool": name, "arguments": dict}``."""
    messages = [m for m in body.get("messages", []) if isinstance(m, dict)]
    if any(m.get("role") == "system" and TITLE_MARKER in _text_of(m) for m in messages):
        return {"text": "fake title"}
    last = messages[-1] if messages else {}
    if last.get("role") == "tool":
        return {"text": DONE_TEXT}
    text = _text_of(last)
    if RUN_MARKER in text:
        command = text.split(RUN_MARKER, 1)[1].strip().splitlines()[0]
        return {"tool": "shell", "arguments": {"command": command}}
    return {"text": "fake reply"}


def _chunk(delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


def stream_events(reply: dict[str, Any]) -> list[dict[str, Any]]:
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    if "tool" in reply:
        call = {
            "index": 0,
            "id": "call_fake_1",
            "type": "function",
            "function": {
                "name": reply["tool"],
                "arguments": json.dumps(reply["arguments"]),
            },
        }
        events = [
            _chunk({"role": "assistant", "content": None, "tool_calls": [call]}),
            _chunk({}, "tool_calls"),
        ]
    else:
        events = [
            _chunk({"role": "assistant", "content": reply["text"]}),
            _chunk({}, "stop"),
        ]
    events.append({**_chunk({}), "choices": [], "usage": usage})
    return events


def full_response(reply: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": reply.get("text")}
    finish = "stop"
    if "tool" in reply:
        message["tool_calls"] = [
            {
                "id": "call_fake_1",
                "type": "function",
                "function": {
                    "name": reply["tool"],
                    "arguments": json.dumps(reply["arguments"]),
                },
            }
        ]
        finish = "tool_calls"
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def make_handler(log_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/models"):
                self._json(200, {"object": "list", "data": [{"id": MODEL}]})
            else:
                self._json(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": {"message": "bad json"}})
                return
            with (log_dir / "requests.jsonl").open("a") as log:
                log.write(json.dumps({"path": self.path, "body": body}) + "\n")
            if not self.path.rstrip("/").endswith("/chat/completions"):
                self._json(404, {"error": {"message": "not found"}})
                return
            reply = plan_reply(body)
            if not body.get("stream"):
                self._json(200, full_response(reply))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in stream_events(reply):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def main() -> int:
    log_dir = Path(sys.argv[1])
    log_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(log_dir))
    print(f"PORT {server.server_address[1]}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
