"""Sandboxed tests for harness/install.sh.

Every run gets a temporary HOME and a fake harness tree, so the real
~/.agents and ~/.config/goose are never touched. Each absence assertion is
paired with a presence assertion from the same run, so a script that silently
did nothing cannot pass.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).resolve().parents[1]
INSTALL_SH = HARNESS_DIR / "install.sh"
AGREEMENT_COPY = "agent-harness-working-agreement.md"
BLOCK_BEGIN = "# >>> agent-harness (managed by harness/install.sh; remove with --uninstall) >>>"

# Port of FILE_REFERENCE_REGEX in crates/goose/src/hints/import_files.rs.
GOOSE_FILE_REFERENCE = re.compile(
    r"(?:^|\s)@([a-zA-Z0-9_\-./]+(?:\.[a-zA-Z0-9]+)+|[A-Z][a-zA-Z0-9_\-]*|[a-zA-Z0-9_\-./]*[./][a-zA-Z0-9_\-./]*)"
)

MCP_TEMPLATE = """{
  "mcpServers": {
    "coord": {
      "command": "uv",
      "args": ["run", "--project", "${HARNESS_ROOT}/coord", "harness-coord-mcp"]
    }
  }
}
"""


def make_harness(root: Path, *, slices: bool = True) -> Path:
    root.mkdir(parents=True)
    shutil.copy2(INSTALL_SH, root / "install.sh")
    (root / "WORKING_AGREEMENT.md").write_text("# Working agreement\nclaim before work\n")
    if not slices:
        return root
    agents = root / "agents"
    agents.mkdir()
    (agents / "dev.md").write_text("---\nname: dev\ndescription: builds\n---\nbody\n")
    (agents / "qa.md").write_text("---\nname: qa\ndescription: reviews\n---\nbody\n")
    (agents / "README.md").write_text("# not an agent\n")
    recipes = root / "recipes"
    (recipes / "sub").mkdir(parents=True)
    (recipes / "tests").mkdir()
    (recipes / "bmad-plan.yaml").write_text("title: plan\n")
    (recipes / "sub" / "analyst.yaml").write_text("title: analyst\n")
    (recipes / "tests" / "fixture.yaml").write_text("title: fixture\n")
    checks = root / "checks"
    checks.mkdir()
    (checks / "scope-creep.md").write_text("---\nname: scope-creep\n---\ncheck\n")
    (checks / "README.md").write_text("# docs\n")
    plugin = root / "plugin" / "agent-harness"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name": "agent-harness"}\n')
    (plugin / ".mcp.json.template").write_text(MCP_TEMPLATE)
    return root


@pytest.fixture
def sandbox(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    harness = make_harness(tmp_path / "harness")
    return home, harness.resolve()


def run(home: Path, harness: Path, *args: str, extra_env: dict[str, str] | None = None):
    env = {
        "HOME": str(home),
        "PATH": os.environ["PATH"],
        "TMPDIR": str(home.parent),
    }
    env.update(extra_env or {})
    result = subprocess.run(
        ["bash", str(harness / "install.sh"), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def snapshot(*roots: Path) -> dict[str, tuple]:
    state: dict[str, tuple] = {}
    for root in roots:
        for path in sorted([root, *root.rglob("*")]):
            stat = path.lstat()
            if path.is_symlink():
                state[str(path)] = ("link", os.readlink(path))
            elif path.is_dir():
                state[str(path)] = ("dir",)
            else:
                state[str(path)] = ("file", path.read_bytes(), stat.st_mtime_ns)
    return state


def config_dir(home: Path) -> Path:
    return home / ".config" / "goose"


def manifest(home: Path) -> Path:
    return home / ".local" / "state" / "agent-harness" / "install-manifest.tsv"


def test_install_creates_expected_artifacts(sandbox):
    home, harness = sandbox
    run(home, harness)

    agents = home / ".agents" / "agents"
    assert (agents / "dev.md").read_text() == (harness / "agents" / "dev.md").read_text()
    assert (agents / "qa.md").is_file()
    assert not (agents / "dev.md").is_symlink(), "goose opens agent files with O_NOFOLLOW"
    assert not (agents / "README.md").exists()

    recipes = home / ".agents" / "recipes"
    assert (recipes / "bmad-plan.yaml").is_file()
    assert (recipes / "sub" / "analyst.yaml").is_file()
    assert not (recipes / "tests").exists()

    checks = home / ".config" / "goose" / "checks"
    assert (checks / "scope-creep.md").is_file()
    assert not (checks / "README.md").exists()

    link = home / ".agents" / "plugins" / "agent-harness"
    assert link.is_symlink()
    assert Path(os.readlink(link)) == harness / "plugin" / "agent-harness"

    rendered = (harness / "plugin" / "agent-harness" / ".mcp.json").read_text()
    assert f"{harness}/coord" in rendered
    assert "${HARNESS_ROOT}" not in rendered

    copy = config_dir(home) / AGREEMENT_COPY
    assert copy.read_text() == (harness / "WORKING_AGREEMENT.md").read_text()
    hints = (config_dir(home) / ".goosehints").read_text()
    assert BLOCK_BEGIN in hints
    assert f"@{AGREEMENT_COPY}" in hints
    assert manifest(home).is_file()


def test_hints_block_is_an_include_goose_will_parse(sandbox):
    home, harness = sandbox
    run(home, harness)
    hints = (config_dir(home) / ".goosehints").read_text()
    references = GOOSE_FILE_REFERENCE.findall(hints)
    assert references == [AGREEMENT_COPY]
    assert (config_dir(home) / references[0]).is_file()


def test_real_working_agreement_has_no_accidental_includes():
    text = (HARNESS_DIR / "WORKING_AGREEMENT.md").read_text()
    assert "claim" in text.lower()
    assert GOOSE_FILE_REFERENCE.findall(text) == []


def test_second_run_is_a_noop(sandbox):
    home, harness = sandbox
    run(home, harness)
    before = snapshot(home, harness)
    second = run(home, harness)
    assert snapshot(home, harness) == before
    assert "nothing to do" in second.stdout
    assert (home / ".agents" / "agents" / "dev.md").is_file()


def test_dry_run_changes_nothing(sandbox):
    home, harness = sandbox
    before = snapshot(home, harness)
    result = run(home, harness, "--dry-run")
    assert snapshot(home, harness) == before
    assert "[dry-run] would copy" in result.stdout
    assert "[dry-run] would link" in result.stdout


def test_dry_run_uninstall_changes_nothing(sandbox):
    home, harness = sandbox
    run(home, harness)
    before = snapshot(home, harness)
    result = run(home, harness, "--uninstall", "--dry-run")
    assert snapshot(home, harness) == before
    assert "[dry-run] would remove" in result.stdout


def test_foreign_files_are_not_overwritten(sandbox):
    home, harness = sandbox
    agents = home / ".agents" / "agents"
    agents.mkdir(parents=True)
    (agents / "dev.md").write_text("mine, not the harness's\n")
    foreign_plugin = home / ".agents" / "plugins" / "agent-harness"
    foreign_plugin.mkdir(parents=True)
    (foreign_plugin / "plugin.json").write_text("{}\n")

    result = run(home, harness)

    assert (agents / "dev.md").read_text() == "mine, not the harness's\n"
    assert not foreign_plugin.is_symlink()
    assert (foreign_plugin / "plugin.json").read_text() == "{}\n"
    assert "skipping" in result.stderr
    assert (agents / "qa.md").is_file()


def test_file_edited_after_install_is_not_overwritten(sandbox):
    home, harness = sandbox
    run(home, harness)
    installed = home / ".agents" / "agents" / "dev.md"
    installed.write_text("local edit\n")
    (harness / "agents" / "dev.md").write_text("---\nname: dev\n---\nv2\n")
    (harness / "agents" / "qa.md").write_text("---\nname: qa\n---\nv2\n")

    result = run(home, harness)

    assert installed.read_text() == "local edit\n"
    assert "edited since install" in result.stderr
    assert (home / ".agents" / "agents" / "qa.md").read_text().endswith("v2\n")


def test_unmodified_copy_is_updated_from_source(sandbox):
    home, harness = sandbox
    run(home, harness)
    (harness / "WORKING_AGREEMENT.md").write_text("# Working agreement v2\n")
    run(home, harness)
    assert (config_dir(home) / AGREEMENT_COPY).read_text() == "# Working agreement v2\n"


def test_artifact_whose_source_is_gone_is_pruned(sandbox):
    home, harness = sandbox
    run(home, harness)
    (harness / "agents" / "qa.md").unlink()
    run(home, harness)
    assert not (home / ".agents" / "agents" / "qa.md").exists()
    assert (home / ".agents" / "agents" / "dev.md").is_file()


def test_uninstall_removes_only_its_own_artifacts(sandbox):
    home, harness = sandbox
    config_dir(home).mkdir(parents=True)
    user_hints = "Always answer in British English.\n"
    (config_dir(home) / ".goosehints").write_text(user_hints)
    agents = home / ".agents" / "agents"
    agents.mkdir(parents=True)
    (agents / "personal.md").write_text("---\nname: personal\n---\nmine\n")

    run(home, harness)
    assert BLOCK_BEGIN in (config_dir(home) / ".goosehints").read_text()
    assert (agents / "dev.md").is_file()

    run(home, harness, "--uninstall")

    assert (config_dir(home) / ".goosehints").read_text() == user_hints
    assert (agents / "personal.md").read_text() == "---\nname: personal\n---\nmine\n"
    assert not (agents / "dev.md").exists()
    assert not (agents / "qa.md").exists()
    assert not (home / ".agents" / "plugins" / "agent-harness").exists()
    assert not (config_dir(home) / AGREEMENT_COPY).exists()
    assert not (harness / "plugin" / "agent-harness" / ".mcp.json").exists()
    assert (harness / "plugin" / "agent-harness" / ".mcp.json.template").is_file()
    assert not manifest(home).exists()


def test_uninstall_after_clean_home_leaves_home_as_found(sandbox):
    home, harness = sandbox
    before = snapshot(home)
    run(home, harness)
    assert (home / ".agents").is_dir()
    run(home, harness, "--uninstall")
    assert snapshot(home) == before


def test_uninstall_keeps_a_copy_edited_after_install(sandbox):
    home, harness = sandbox
    run(home, harness)
    edited = home / ".agents" / "recipes" / "bmad-plan.yaml"
    edited.write_text("title: tuned locally\n")
    result = run(home, harness, "--uninstall")
    assert edited.read_text() == "title: tuned locally\n"
    assert "keeping" in result.stderr
    assert not (home / ".agents" / "recipes" / "sub" / "analyst.yaml").exists()


def test_missing_slices_are_skipped_with_a_message(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    harness = make_harness(tmp_path / "harness", slices=False).resolve()
    result = run(home, harness)
    for name in ("agents", "recipes", "checks", "plugin"):
        assert f"skip {name}" in result.stdout
    assert not (home / ".agents").exists()
    assert (config_dir(home) / AGREEMENT_COPY).is_file()


def test_default_harness_root_is_the_script_directory(sandbox):
    home, harness = sandbox
    result = run(home, harness)
    assert f"harness root: {harness}" in result.stdout
    assert (home / ".agents" / "agents" / "dev.md").is_file()


def test_xdg_config_home_moves_hints_but_not_checks(sandbox, tmp_path):
    home, harness = sandbox
    xdg = tmp_path / "xdg"
    run(home, harness, extra_env={"XDG_CONFIG_HOME": str(xdg)})
    assert (xdg / "goose" / AGREEMENT_COPY).is_file()
    assert not (config_dir(home) / AGREEMENT_COPY).exists()
    # checks/mod.rs global_checks_dirs() hard-codes $HOME/.config/goose/checks.
    assert (config_dir(home) / "checks" / "scope-creep.md").is_file()


def test_goose_path_root_moves_config_and_plugins(sandbox, tmp_path):
    home, harness = sandbox
    root = tmp_path / "goose-root"
    run(home, harness, extra_env={"GOOSE_PATH_ROOT": str(root)})
    assert (root / "config" / AGREEMENT_COPY).is_file()
    assert (root / ".agents" / "plugins" / "agent-harness").is_symlink()
    assert not (home / ".agents" / "plugins").exists()
    assert (home / ".agents" / "agents" / "dev.md").is_file()


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean():
    result = subprocess.run(
        ["shellcheck", str(INSTALL_SH)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout


# --- the `harness-coord` CLI (uv tool) -------------------------------------
#
# Recipes call `harness-coord verify-assertions`, so install.sh puts the CLI on
# PATH with `uv tool install --editable <harness>/coord`. Every test below
# sandboxes uv's tool and bin dirs; the uv cache and managed Pythons are shared
# with the real ones so no test needs the network.

UV = shutil.which("uv")
needs_uv = pytest.mark.skipif(UV is None, reason="uv not installed")


def _uv_dir(*args: str) -> str:
    assert UV is not None
    return subprocess.run(
        [UV, *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_cli_project(root: Path, output: str) -> Path:
    """A tiny project that provides a `harness-coord` script printing `output`."""
    (root / "src" / "fake_coord").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "harness-coord"\nversion = "0.1.0"\n'
        'requires-python = ">=3.12"\n'
        '[project.scripts]\nharness-coord = "fake_coord:main"\n'
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
        '[tool.hatch.build.targets.wheel]\npackages = ["src/fake_coord"]\n'
    )
    (root / "src" / "fake_coord" / "__init__.py").write_text(
        f"def main():\n    print({output!r})\n"
    )
    return root


@pytest.fixture
def uv_sandbox(tmp_path: Path, sandbox):
    home, harness = sandbox
    make_cli_project(harness / "coord", "harness coord cli")
    tools, bin_dir = tmp_path / "uv-tools", tmp_path / "uv-bin"
    env = {
        "UV_TOOL_DIR": str(tools),
        "UV_TOOL_BIN_DIR": str(bin_dir),
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or _uv_dir("cache", "dir"),
        "UV_PYTHON_INSTALL_DIR": _uv_dir("python", "dir"),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    }
    return home, harness, bin_dir, env


def uv_tools(env: dict[str, str]) -> str:
    assert UV is not None
    return subprocess.run(
        [UV, "tool", "list"],
        env={**os.environ, **env},
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def run_cli(bin_dir: Path) -> str:
    return subprocess.run(
        [str(bin_dir / "harness-coord")], check=True, capture_output=True, text=True
    ).stdout.strip()


@needs_uv
def test_coord_cli_is_installed_on_path_and_recorded(uv_sandbox):
    home, harness, bin_dir, env = uv_sandbox
    result = run(home, harness, extra_env=env)
    assert "install harness-coord CLI" in result.stdout
    assert run_cli(bin_dir) == "harness coord cli"
    assert "harness-coord v0.1.0" in uv_tools(env)
    assert f"tool\tharness-coord\t{harness / 'coord'}" in manifest(home).read_text()


@needs_uv
def test_coord_cli_second_run_is_a_noop(uv_sandbox):
    home, harness, bin_dir, env = uv_sandbox
    run(home, harness, extra_env=env)
    before = snapshot(home, bin_dir)
    second = run(home, harness, extra_env=env)
    assert "nothing to do" in second.stdout
    assert snapshot(home, bin_dir) == before
    assert run_cli(bin_dir) == "harness coord cli"


@needs_uv
def test_coord_cli_dry_run_installs_nothing(uv_sandbox):
    home, harness, bin_dir, env = uv_sandbox
    result = run(home, harness, "--dry-run", extra_env=env)
    assert "[dry-run] would install harness-coord CLI" in result.stdout
    assert not bin_dir.exists()
    assert "harness-coord" not in uv_tools(env)


@needs_uv
def test_coord_cli_is_removed_by_uninstall(uv_sandbox):
    home, harness, bin_dir, env = uv_sandbox
    run(home, harness, extra_env=env)
    assert (bin_dir / "harness-coord").exists()
    dry = run(home, harness, "--uninstall", "--dry-run", extra_env=env)
    assert "[dry-run] would uninstall harness-coord CLI" in dry.stdout
    assert (bin_dir / "harness-coord").exists()
    run(home, harness, "--uninstall", extra_env=env)
    assert not (bin_dir / "harness-coord").exists()
    assert "harness-coord" not in uv_tools(env)


@needs_uv
def test_foreign_coord_tool_is_never_touched(uv_sandbox, tmp_path):
    home, harness, bin_dir, env = uv_sandbox
    foreign = make_cli_project(tmp_path / "foreign", "someone else's coord")
    assert UV is not None
    subprocess.run(
        [UV, "tool", "install", "-q", str(foreign)],
        env={**os.environ, **env},
        check=True,
        capture_output=True,
    )
    result = run(home, harness, extra_env=env)
    assert "harness-coord" in result.stderr and "not by this installer" in result.stderr
    assert run_cli(bin_dir) == "someone else's coord"
    assert "tool\tharness-coord" not in manifest(home).read_text()
    run(home, harness, "--uninstall", extra_env=env)
    assert run_cli(bin_dir) == "someone else's coord"


def test_foreign_coord_on_path_is_left_alone(sandbox, tmp_path):
    home, harness = sandbox
    make_cli_project(harness / "coord", "harness coord cli")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "harness-coord").write_text("#!/bin/sh\necho mine\n")
    (elsewhere / "harness-coord").chmod(0o755)
    result = run(
        home,
        harness,
        extra_env={
            "UV_TOOL_DIR": str(tmp_path / "uv-tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "uv-bin"),
            "PATH": f"{elsewhere}{os.pathsep}{os.environ['PATH']}",
        },
    )
    assert "already on PATH" in result.stderr
    assert not (tmp_path / "uv-bin").exists()
    assert (elsewhere / "harness-coord").read_text() == "#!/bin/sh\necho mine\n"


def test_coord_cli_skipped_with_a_warning_when_uv_is_missing(sandbox, tmp_path):
    home, harness = sandbox
    make_cli_project(harness / "coord", "harness coord cli")
    assert shutil.which("uv", path="/usr/bin:/bin") is None
    result = run(
        home,
        harness,
        extra_env={"PATH": "/usr/bin:/bin", "UV_TOOL_BIN_DIR": str(tmp_path / "uv-bin")},
    )
    assert "uv not found" in result.stderr
    assert not (tmp_path / "uv-bin").exists()
    assert (home / ".agents" / "agents" / "dev.md").is_file()


@needs_uv
def test_coord_cli_follows_a_moved_harness_tree(uv_sandbox, tmp_path):
    home, harness, bin_dir, env = uv_sandbox
    run(home, harness, extra_env=env)
    moved = make_harness(tmp_path / "moved").resolve()
    make_cli_project(moved / "coord", "moved coord cli")
    result = run(home, moved, extra_env=env)
    assert "reinstall harness-coord CLI" in result.stdout
    assert run_cli(bin_dir) == "moved coord cli"
    assert f"tool\tharness-coord\t{moved / 'coord'}" in manifest(home).read_text()
