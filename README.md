# Codex Telegram Bot

A small Telegram bot that lets one allowed Telegram user send coding tasks to
`codex exec` inside a configured Git repository.

The bot runs one Codex task at a time, uses `workspace-write` sandboxing, and
splits long Codex output into Telegram-sized replies.

## Prerequisites

- Python 3.13 or compatible Python 3 version
- uv installed and available on `PATH`
- Git installed and available on `PATH`
- Codex CLI installed and available on `PATH`
- A Telegram bot token from BotFather
- The numeric Telegram user ID that should be allowed to use the bot
- A target project directory that is a Git repository

## Setup

Create the virtual environment and install dependencies:

```bash
uv sync
```

Create a `.env` file in this directory:

```dotenv
TELEGRAM_BOT_TOKEN="your_bot_token_here"
ALLOWED_USER_ID="123456789"
PROJECT_DIR="/absolute/path/to/your/git/repo"
```

`PROJECT_DIR` must exist and must be a Git worktree. The bot refuses to start if
the token, allowed user ID, Git, or project directory check fails.

## Running

Start the bot:

```bash
uv run python bot.py
```

In Telegram, send `/start` to the bot from the allowed account, then send a
coding task as a normal text message.

## Troubleshooting

### `telegram.error.Conflict: terminated by other getUpdates request`

Telegram allows only one active polling client per bot token. If you see this
error, another process, server, or bot framework is already using the same
`TELEGRAM_BOT_TOKEN`.

Check for another local copy:

```bash
ps aux | grep '[p]ython3 bot.py'
```

Stop the extra process, or stop any deployment/service using the same bot token,
then start only one copy:

```bash
uv run python bot.py
```

If the conflicting process is running on another machine, this repository cannot
stop it automatically. Shut down that deployment, or create a separate bot token
in BotFather for the second environment.

## Behavior

- Only `ALLOWED_USER_ID` can use the bot.
- Only one Codex task runs at a time. If another message arrives while Codex is
  busy, the bot replies that a task is already running.
- Only one local `bot.py` process can run at a time. The bot writes `.bot.lock`
  while running to catch accidental duplicate local starts.
- Codex runs in `PROJECT_DIR` with:

```bash
codex exec --cd "$PROJECT_DIR" --skip-git-repo-check --sandbox workspace-write --ephemeral "<your prompt>"
```

- The subprocess receives a small allowlisted environment so Telegram secrets
  are not passed through to Codex.
- A Codex task times out after 900 seconds.

## Notes

Make sure the user running `uv run python bot.py` can access both the Codex CLI
configuration and the target `PROJECT_DIR`.
