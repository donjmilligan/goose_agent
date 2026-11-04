#!/usr/bin/env python3
"""Stop hook: desktop notification when goose finishes a turn.

Port of vanta-lms `.claude/hooks/notify.py`. macOS uses terminal-notifier
when installed and falls back to osascript; Linux uses notify-send; Windows
uses a PowerShell toast. The message travels as an argument or environment
variable, never spliced into a script, so quotes in it cannot break out.

goose treats `Stop` as a blocking event (`emit_blocking` in
crates/goose/src/hooks/mod.rs): exit 2 or a `{"decision":"block"}` on stdout
would force the turn to continue. So this script writes nothing to stdout,
swallows every error and always exits 0. Set `HARNESS_NOTIFY=0` to silence it.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Any

TITLE = "goose"
DEFAULT_MESSAGE = "Turn finished"
MAX_MESSAGE = 160
NOTIFY_TIMEOUT_S = 5

WINDOWS_TOAST = (
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
    "ContentType = WindowsRuntime] | Out-Null; "
    "$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
    "$x = $t.GetElementsByTagName('text'); "
    "$x[0].AppendChild($t.CreateTextNode($env:HARNESS_NOTIFY_TITLE)) | Out-Null; "
    "$x[1].AppendChild($t.CreateTextNode($env:HARNESS_NOTIFY_MESSAGE)) | Out-Null; "
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "$env:HARNESS_NOTIFY_TITLE).Show([Windows.UI.Notifications.ToastNotification]::new($t))"
)
OSASCRIPT = [
    "-e",
    "on run argv",
    "-e",
    "display notification (item 1 of argv) with title (item 2 of argv)",
    "-e",
    "end run",
]


def notification_command(
    system: str, message: str, title: str, which: Callable[[str], str | None]
) -> tuple[list[str], dict[str, str]] | None:
    """(argv, extra env) for this platform, or None when nothing can notify."""
    if system == "darwin":
        notifier = which("terminal-notifier")
        if notifier:
            return [notifier, "-title", title, "-message", message, "-sound", "default"], {}
        osascript = which("osascript")
        if osascript:
            return [osascript, *OSASCRIPT, message, title], {}
    elif system == "linux":
        notify_send = which("notify-send")
        if notify_send:
            return [notify_send, title, message, "--expire-time=10000"], {}
    elif system == "windows":
        powershell = which("powershell") or which("pwsh")
        if powershell:
            return [powershell, "-NoProfile", "-Command", WINDOWS_TOAST], {
                "HARNESS_NOTIFY_TITLE": title,
                "HARNESS_NOTIFY_MESSAGE": message,
            }
    return None


def message_from_payload(raw: str) -> str:
    try:
        payload: Any = json.loads(raw)
    except ValueError:
        return DEFAULT_MESSAGE
    text = payload.get("last_assistant_message") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return DEFAULT_MESSAGE
    text = " ".join(text.split())
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


def notify(
    message: str,
    title: str = TITLE,
    system: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    env: Mapping[str, str] = os.environ,
) -> bool:
    command = notification_command(system or platform.system().lower(), message, title, which)
    if command is None:
        return False
    argv, extra_env = command
    subprocess.run(
        argv,
        env={**env, **extra_env},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=NOTIFY_TIMEOUT_S,
        check=False,
    )
    return True


def main() -> int:
    try:
        if os.environ.get("HARNESS_NOTIFY", "1") == "0":
            return 0
        raw = "" if sys.stdin.isatty() else sys.stdin.read()
        notify(message_from_payload(raw))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
