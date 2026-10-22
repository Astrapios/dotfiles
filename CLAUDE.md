# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Personal dotfiles repo with shell/editor/tmux configs. Uses symlink-based installation (no GNU Stow). (The `astra` Telegram bridge that used to live here was extracted to its own repo at `~/src/astra`.)

## Key Commands

```bash
# Install/setup
./install.zsh          # Interactive setup (creates symlinks, installs deps)
./install.zsh -y       # Non-interactive (auto-yes)

```

## Architecture

### astra (Telegram bridge) — moved out

The `astra` Telegram bridge now lives in its own repo at **`~/src/astra`**
(`git@github.com:Astrapios/astra.git`) with its own installer, tests, and docs.
It is no longer part of this repo. The hook symlinks below still wire Claude
Code / Gemini to `astra hook`, which resolves via the `~/bin/astra` PATH wrapper
that astra's own `install.sh` creates.

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

### ttyd (web terminal)

`installers/install_ttyd.zsh` builds ttyd from upstream source via
`installers/ttyd/build_ttyd.zsh` and applies `installers/ttyd/patches/`
(OSC 52 clipboard on WebKit/iOS, ctrl-c on iOS hardware keyboards). The
release binary must not be used: it predates OSC 52 support. The matching
tmux side (Ms override forcing selection `c`) is in `.tmux.conf`. Rationale
and update procedure: `installers/ttyd/README.md`.

### Config Files

- `.tmux.conf` — Prefix is Ctrl+A, smart pane switching with Ctrl+hjkl, `|`/`_` for splits
- `.zshrc` — Antidote plugin manager, Pure prompt, FZF integration, custom funcs from `zsh_funcs/`
- `.vimrc` — vim-plug, FZF, ALE linter, quantum theme
- `code-server/` — VS Code Server settings and keybindings
- `.gitconfig`, `config/git/ignore` — git user, riff pager, git-spice forge, glab credential helper; global ignore
- `config/mise/config.toml` — mise tool versions (git-spice via ubi); `install.zsh` runs `mise install`
- `pixi_tools/<tool>/` — personal pixi tools (`~/pixi_tools` is a symlink to this directory; `.pixi` envs are gitignored and built in place)

### Claude Code (all symlinked by `install.zsh`)

- `scripts/claude_global.md` -> `~/.claude/CLAUDE.md` (always-on rules; procedures live in skills), `scripts/claude_rtk.md` -> `~/.claude/RTK.md` (imported by it)
- `scripts/claude_settings.json` -> `~/.claude/settings.json` (model, hooks, promoted permissions), `scripts/claude_keybindings.json` -> `~/.claude/keybindings.json`
- `scripts/claude/skills/<name>/` -> `~/.claude/skills/<name>`: `mr-review` (MR review with glab scripts, references, evals), `html-report` (self-contained dark HTML decks and reports, screenshot self-review), `glb-view` (ECEF GLB to S3 plus viewer link), `long-run` (background compute jobs with check-ins), `deeper`, `resolve-conflicts`, `vault-sync`
- `scripts/claude/agents/<name>.md` -> `~/.claude/agents/`: `sar-physics-reviewer`
- `scripts/claude/hooks/*.py|sh` -> `~/.claude/hooks/`: `guard_gitlab_publish.py` (PreToolUse guard: no publishing, plain notes or thread resolution on MRs)
