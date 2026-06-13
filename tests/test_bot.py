import importlib
import subprocess
import sys
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
