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

### Config Files

- `.tmux.conf` — Prefix is Ctrl+A, smart pane switching with Ctrl+hjkl, `|`/`_` for splits
- `.zshrc` — Antidote plugin manager, Pure prompt, FZF integration, custom funcs from `zsh_funcs/`
- `.vimrc` — vim-plug, FZF, ALE linter, quantum theme
- `code-server/` — VS Code Server settings and keybindings
