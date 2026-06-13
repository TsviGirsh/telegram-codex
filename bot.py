import asyncio
import fcntl
import os
import re
import signal
import subprocess
from datetime import date
from pathlib import Path

from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from dotenv import load_dotenv

load_dotenv()

CODEX_TIMEOUT_SECONDS = 900
REPLY_CHUNK_SIZE = 3900
BOT_DIR = Path(__file__).resolve().parent
BOT_LOCK_FILE = BOT_DIR / ".bot.lock"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID_RAW = os.getenv("ALLOWED_USER_ID")
PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/home/dev/my-proj")).expanduser().resolve()
codex_lock = asyncio.Lock()
BRANCH_NAME_PATTERN = re.compile(r"^[a-z]{3}\d{1,2}$")


def validate_repo_writable(repo_dir: Path) -> None:
    repo_dir = repo_dir.expanduser().resolve()
    if repo_dir != PROJECT_DIR:
        raise RuntimeError(f"repo_dir must be PROJECT_DIR: {PROJECT_DIR}")

    checks = [
        repo_dir / ".codex_write_test",
        repo_dir / ".git" / ".codex_git_write_test",
    ]

    for path in checks:
        try:
            path.write_text("ok")
            path.unlink()
        except Exception as exc:
            raise RuntimeError(f"Bot cannot write to {path}: {exc}") from exc


def branch_name_for_today(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.strftime('%b').lower()}{today.day}"


def create_git_branch(branch_name: str) -> None:
    if not BRANCH_NAME_PATTERN.fullmatch(branch_name):
        raise RuntimeError(
            "branch_name must use monthday format, for example 'jun14'."
        )

    branch_ref = f"refs/heads/{branch_name}"
    branch_exists = subprocess.run(
        ["git", "-C", str(PROJECT_DIR), "show-ref", "--verify", "--quiet", branch_ref],
        check=False,
    )
    if branch_exists.returncode == 0:
        cmd = ["git", "-C", str(PROJECT_DIR), "switch", branch_name]
    else:
        cmd = ["git", "-C", str(PROJECT_DIR), "switch", "-c", branch_name]

    result = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        error = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Failed to create Git branch {branch_name}: {error}")


def require_config() -> int:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN must be set.")

    if not ALLOWED_USER_ID_RAW:
        raise RuntimeError("ALLOWED_USER_ID must be set.")

    try:
        allowed_user_id = int(ALLOWED_USER_ID_RAW)
    except ValueError as exc:
        raise RuntimeError("ALLOWED_USER_ID must be a numeric Telegram user ID.") from exc

    if not PROJECT_DIR.is_dir():
        raise RuntimeError(f"PROJECT_DIR does not exist or is not a directory: {PROJECT_DIR}")

    try:
        git_check = subprocess.run(
            ["git", "-C", str(PROJECT_DIR), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git must be installed and available on PATH.") from exc
    if git_check.returncode != 0 or git_check.stdout.strip() != "true":
        raise RuntimeError(f"PROJECT_DIR must be a Git repository: {PROJECT_DIR}")

    validate_repo_writable(PROJECT_DIR)

    return allowed_user_id


ALLOWED_USER_ID = require_config()


def acquire_instance_lock():
    lock_file = BOT_LOCK_FILE.open("w")

    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError(
            "Another local bot.py process is already running. Stop that process before starting "
            "this bot again."
        ) from exc

    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def codex_env() -> dict[str, str]:
    allowed_keys = {
        "CODEX_HOME",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOGNAME",
        "PATH",
        "SHELL",
        "TERM",
        "USER",
    }

    return {
        key: value
        for key, value in os.environ.items()
        if key in allowed_keys or key.startswith("CODEX_")
    }


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()


async def run_codex(prompt: str) -> str:
    create_git_branch(branch_name_for_today())

    cmd = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        prompt,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(PROJECT_DIR),
        env=codex_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=CODEX_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await stop_process(process)
        return "Codex task timed out."

    result = stdout.decode(errors="replace").strip()
    errors = stderr.decode(errors="replace").strip()

    if process.returncode != 0:
        return f"Codex failed.\n\nSTDERR:\n{errors[-3000:]}"

    if not result:
        result = errors

    return result


async def reply_long_text(update: Update, text: str) -> None:
    if not update.message:
        return

    if not text:
        await update.message.reply_text("(No output.)")
        return

    for index in range(0, len(text), REPLY_CHUNK_SIZE):
        await update.message.reply_text(text[index:index + REPLY_CHUNK_SIZE])


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    return user is not None and user.id == ALLOWED_USER_ID


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    if not update.message:
        return

    await update.message.reply_text(
        "Send me a coding task. Example:\n"
        "Fix failing tests and explain the diff."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return

    if not update.message or not update.message.text:
        return

    prompt = update.message.text.strip()
    if not prompt:
        await update.message.reply_text("Send a non-empty coding task.")
        return

    if codex_lock.locked():
        await update.message.reply_text(
            "Codex is already running a task. Try again when it finishes."
        )
        return

    await codex_lock.acquire()

    try:
        await update.message.reply_text("Running Codex...")
        result = await run_codex(prompt)
    finally:
        codex_lock.release()

    await reply_long_text(update, result)


def main():
    instance_lock = acquire_instance_lock()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("bot is running, see the provided Telegram Channel...")

    try:
        app.run_polling(drop_pending_updates=True)
    except Conflict as exc:
        raise RuntimeError(
            "Telegram rejected polling because another process is already calling getUpdates "
            "with this bot token. Stop every other running copy of this bot, deployment, or bot "
            "framework using the same TELEGRAM_BOT_TOKEN, then start this bot again."
        ) from exc
    finally:
        instance_lock.close()


if __name__ == "__main__":
    main()
