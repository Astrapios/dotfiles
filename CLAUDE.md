# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Personal dotfiles repo with shell/editor/tmux configs and a Telegram bridge for Claude Code hooks. Uses symlink-based installation (no GNU Stow).

## Key Commands

```bash
# Install/setup
./install.zsh          # Interactive setup (creates symlinks, installs deps)
./install.zsh -y       # Non-interactive (auto-yes)

# Tests
cd scripts/tg-hook && pixi run test # Run tg-hook unit tests

# tg-hook usage
tg-hook listen         # Start Telegram listener daemon
tg-hook hook           # Called by Claude hooks (reads stdin)
tg-hook notify "msg"   # One-shot notification
tg-hook ask "question" # Ask and wait for reply
```

## Architecture

### Telegram Bridge (`scripts/tg-hook/`)

A pip-installable Python package bridging Claude Code sessions to Telegram. Structured as `src/tg_hook/` with modules: `config`, `telegram`, `tmux`, `state`, `content`, `routing`, `signals`, `commands`, `listener`, `cli`. Managed as a pixi project with editable install (`scripts/tg-hook/pixi.toml`). Invoked via `tg-hook <cmd>` (`~/bin/tg-hook` wrapper delegates to `pixi run -m`).

**Module import pattern:** Submodules import peers as objects (`from tg_hook import config, telegram`) and call `telegram.tg_send(msg)`. This enables mock patching via `patch.object(tg.<module>, "func")`. The `__init__.py` re-exports everything for backward compat.

**Signal-based architecture:**
- Claude hooks (configured in `scripts/claude_settings.json`) invoke `tg-hook hook` which writes JSON signal files to `/tmp/tg_hook_signals/`
- `tg-hook listen` is the only process that talks to Telegram. It polls for signals and user messages in a single loop
- State files prefixed with `_` (e.g., `_active_prompt_w4.json`, `_bash_cmd_w4.json`) persist across signal cleanup and auto-reload

**Key subsystems:**
- **Session scanning**: Detects Claude instances via `tmux list-panes`, routes messages by `wN` prefix
- **Permission handling**: Extracts permission dialogs from tmux pane content (`_extract_pane_permission`), sends to Telegram with options, translates replies into arrow-key navigation via `tmux send-keys`
- **Stop capture**: Finds response between last text `●` and last `❯` in pane content
- **Content cleaning**: `_filter_noise` strips UI chrome (separators, spinners, mode indicators)
- **Auto-reload**: Listener detects file changes via mtime and `os.execv`s itself

**Markdown V1 safety:** All dynamic content (project names, filenames, user text) must be inside backtick code spans or ``` pre blocks to prevent underscores from breaking Telegram's Markdown parser.

**tmux send-keys pattern:** Arrow key commands must be sent in a single `tmux send-keys` call (e.g., `tmux send-keys -t pane Down Down Enter`), with `sleep 0.1` between navigation and action via `bash -c` chaining. Individual subprocess calls are too slow and keys get dropped.

### Versioning

tg-hook uses semver pre-1.0. **On every commit touching `scripts/tg-hook/`**, check whether a version bump is needed and include it in the same commit:
- **MINOR** (0.X.0): new user-facing feature — new CLI command, new Telegram command, new public API function
- **PATCH** (0.0.X): bug fixes, refactors, test-only or docs-only changes

Always update both `pyproject.toml` version and `CHANGELOG.md` together with the change.

### Credentials

Telegram secrets stored in `~/.config/tg_hook.env` (not tracked). Hook activation requires env var `CLAUDE_TG_HOOKS=1` (set in `claude_settings.json`).

### Documentation

When adding new commands, aliases, or user-facing features to tg-hook, update all three places:
1. `scripts/tg-hook/README.md` — user-facing documentation
2. `tg-hook help` output in `src/tg_hook/cli.py` (`cmd_help()`)
3. Telegram `/help` output in `src/tg_hook/commands.py` (`_handle_command` help section)

Also update `_set_bot_commands` in `src/tg_hook/telegram.py` if adding a new `/command`, and the `_ALIASES` dict / alias regexes in `commands.py` if adding short aliases.

### Personal Pixi Tools (`~/pixi_tools/`)

Standalone pixi projects for utilities that shouldn't be added to any project repo's dependencies. Each tool lives in its own subdirectory with a `pixi.toml`.

```bash
# Run a script using a pixi tool environment
pixi run --manifest-path ~/pixi_tools/<tool>/pixi.toml python script.py
```

Available tools:
- **`ppt/`** — PowerPoint generation (`python-pptx`)

If a tool directory doesn't exist yet, create it with a `pixi.toml` and run `pixi install`:
```bash
mkdir -p ~/pixi_tools/<tool>
# Write pixi.toml with needed dependencies
cd ~/pixi_tools/<tool> && pixi install
```

### Config Files

- `.tmux.conf` — Prefix is Ctrl+A, smart pane switching with Ctrl+hjkl, `|`/`_` for splits
- `.zshrc` — Antidote plugin manager, Pure prompt, FZF integration, custom funcs from `zsh_funcs/`
- `.vimrc` — vim-plug, FZF, ALE linter, quantum theme
- `code-server/` — VS Code Server settings and keybindings
