from __future__ import annotations

import json
import stat
from pathlib import Path

import notify
import pytest
from conftest import run_script

STOP = {
    "event": "Stop",
    "session_id": "test-session",
    "last_assistant_message": "Done. Tests pass; said \"hi\" 'twice'.",
}
NOTIFIERS = ("terminal-notifier", "osascript", "notify-send", "powershell")


def fake_which(*available: str):
    return lambda name: f"/bin/{name}" if name in available else None


def test_macos_prefers_terminal_notifier() -> None:
    argv, env = notify.notification_command(
        "darwin", "msg", "goose", fake_which("terminal-notifier", "osascript")
    ) or ([], {})
    assert argv[0] == "/bin/terminal-notifier"
    assert argv[argv.index("-message") + 1] == "msg"
    assert env == {}


def test_macos_falls_back_to_osascript_with_message_as_an_argument() -> None:
    message = 'say "hi" & \\ quit'
    argv, _ = notify.notification_command("darwin", message, "goose", fake_which("osascript")) or (
        [],
        {},
    )
    assert argv[0] == "/bin/osascript"
    assert argv[-2:] == [message, "goose"]
    assert all(message not in part for part in argv[:-2])


def test_linux_uses_notify_send() -> None:
    argv, _ = notify.notification_command("linux", "msg", "goose", fake_which("notify-send")) or (
        [],
        {},
    )
    assert argv[:3] == ["/bin/notify-send", "goose", "msg"]


def test_windows_passes_text_through_the_environment_not_the_script() -> None:
    message = "it's done'; Remove-Item C:\\ -Recurse; '"
    argv, env = notify.notification_command(
        "windows", message, "goose", fake_which("powershell")
    ) or ([], {})
    assert argv[0] == "/bin/powershell"
    assert env == {"HARNESS_NOTIFY_TITLE": "goose", "HARNESS_NOTIFY_MESSAGE": message}
    assert all(message not in part for part in argv)


@pytest.mark.parametrize("system", ["darwin", "linux", "windows", "plan9"])
def test_no_notifier_available_means_no_command(system: str) -> None:
    assert notify.notification_command(system, "m", "t", fake_which()) is None
    assert notify.notification_command(system, "m", "t", fake_which(*NOTIFIERS)) is not None or (
        system == "plan9"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (json.dumps(STOP), STOP["last_assistant_message"]),
        (json.dumps({"event": "Stop"}), notify.DEFAULT_MESSAGE),
        (json.dumps({"last_assistant_message": "   "}), notify.DEFAULT_MESSAGE),
        ("not json", notify.DEFAULT_MESSAGE),
        ("[1]", notify.DEFAULT_MESSAGE),
        (json.dumps({"last_assistant_message": "x" * 500}), "x" * 159 + "…"),
    ],
)
def test_message_from_payload(raw: str, expected: str) -> None:
    assert notify.message_from_payload(raw) == expected


def make_stubs(directory: Path, exit_code: int = 0) -> Path:
    """Fake notifiers that record their argv, print to stdout, then exit."""
    log = directory / "calls.log"
    for name in NOTIFIERS:
        stub = directory / name
        stub.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$0 $*" >> "{log}"\n'
            'echo \'{"decision":"block","reason":"stub stdout must not leak"}\'\n'
            f"exit {exit_code}\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return log


def test_stop_hook_notifies_and_keeps_stdout_empty(tmp_path: Path) -> None:
    log = make_stubs(tmp_path)
    result = run_script("notify.py", STOP, env={"PATH": str(tmp_path)})
    assert result.returncode == 0
    assert result.stdout == ""
    assert "Tests pass" in log.read_text()


def test_failing_notifier_is_swallowed(tmp_path: Path) -> None:
    log = make_stubs(tmp_path, exit_code=1)
    result = run_script("notify.py", STOP, env={"PATH": str(tmp_path)})
    assert result.returncode == 0
    assert result.stdout == ""
    assert log.read_text()


def test_missing_notifier_is_swallowed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run_script("notify.py", STOP, env={"PATH": str(empty)})
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_opt_out_skips_the_notifier(tmp_path: Path) -> None:
    log = make_stubs(tmp_path)
    result = run_script("notify.py", STOP, env={"PATH": str(tmp_path), "HARNESS_NOTIFY": "0"})
    assert result.returncode == 0
    assert result.stdout == ""
    assert not log.exists()
    run_script("notify.py", STOP, env={"PATH": str(tmp_path)})
    assert log.exists()


@pytest.mark.parametrize("stdin", ["", "garbage", "{}"])
def test_any_payload_exits_zero_silently(tmp_path: Path, stdin: str) -> None:
    make_stubs(tmp_path)
    result = run_script("notify.py", stdin, env={"PATH": str(tmp_path)})
    assert result.returncode == 0
    assert result.stdout == ""
