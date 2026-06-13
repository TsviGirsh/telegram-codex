import asyncio
import importlib
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def import_bot_with_project_dir(monkeypatch: pytest.MonkeyPatch, project_dir: Path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOWED_USER_ID", "123456789")
    monkeypatch.setenv("PROJECT_DIR", str(project_dir))

    sys.modules.pop("bot", None)
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    return importlib.import_module("bot")


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "project"
    repo_dir.mkdir()
    subprocess.run(
        ["git", "init", str(repo_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo_dir.resolve()


def test_validate_repo_writable_accepts_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)

    bot.validate_repo_writable(bot.PROJECT_DIR)

    assert bot.PROJECT_DIR == project_dir
    assert not (project_dir / ".codex_write_test").exists()
    assert not (project_dir / ".git" / ".codex_git_write_test").exists()


def test_validate_repo_writable_rejects_non_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    with pytest.raises(RuntimeError, match="repo_dir must be PROJECT_DIR"):
        bot.validate_repo_writable(other_dir)


def test_branch_name_for_today_uses_monthday_format(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)

    assert bot.branch_name_for_today(date(2026, 6, 14)) == "jun14"


def test_create_git_branch_creates_branch_on_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[3:6] == ["show-ref", "--verify", "--quiet"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bot.subprocess, "run", fake_run)

    bot.create_git_branch("jun14")

    assert calls[0][0] == [
        "git",
        "-C",
        str(project_dir),
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/jun14",
    ]
    assert calls[1][0] == [
        "git",
        "-C",
        str(project_dir),
        "switch",
        "-c",
        "jun14",
    ]


def test_create_git_branch_rejects_invalid_branch_name(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)

    with pytest.raises(RuntimeError, match="monthday format"):
        bot.create_git_branch("feature/jun14")


def test_run_codex_creates_branch_before_executing_from_project_dir(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> None:
    bot = import_bot_with_project_dir(monkeypatch, project_dir)
    calls = []
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"done", b""

    def fake_create_git_branch(branch_name: str) -> None:
        calls.append(("branch", branch_name))

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        calls.append(("codex", cmd))
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(bot, "branch_name_for_today", lambda: "jun14")
    monkeypatch.setattr(bot, "create_git_branch", fake_create_git_branch)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = asyncio.run(bot.run_codex("fix it"))

    assert result == "done"
    assert calls == [
        ("branch", "jun14"),
        (
            "codex",
            (
                "codex",
                "exec",
                "--sandbox",
                "workspace-write",
                "--ephemeral",
                "fix it",
            ),
        ),
    ]
    assert captured["kwargs"]["cwd"] == str(project_dir)
