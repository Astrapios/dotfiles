"""CLI profile registry for multi-CLI support (Claude, Gemini, etc.)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CLIProfile:
    """Describes a CLI tool's UI patterns, hook events, and tool names."""
    name: str                       # "claude", "gemini"
    display_name: str               # "Claude Code", "Gemini"
    pane_commands: tuple[str, ...]  # ("claude",) or ("node",)
    start_command_pattern: str      # regex to match pane_start_command
    pane_title_pattern: str         # regex to match pane_title (fallback detection)
    prompt_re: str                  # regex for prompt line
    prompt_char: str                # "❯" or ">" (for content boundaries)
    response_bullet: str            # "●" for Claude, "✦" for Gemini
    tool_header_re: str             # r'^● \w+\(' for Claude, r'^[╭│╰]' for Gemini
    busy_indicator: str             # "esc to interr" for Claude, "esc to cancel" for Gemini
    spinner_re: str                 # regex for spinner/thinking lines
    interrupted_pattern: str        # "Interrupted" for Claude
    compacting_pattern: str         # r'[Cc]ompacting' for Claude
    event_map: dict[str, str]       # external→internal event names
    tool_map: dict[str, str]        # external→internal tool names
    restart_cmd: str                # "claude -c" or "gemini -r latest"
    launch_cmd: str                 # "claude" or "gemini"


# --- Profile registry ---

_profiles: dict[str, CLIProfile] = {}


def register_profile(profile: CLIProfile) -> None:
    """Register a CLI profile."""
    _profiles[profile.name] = profile


def get_profile(name: str) -> CLIProfile | None:
    """Look up a profile by name."""
    return _profiles.get(name)


def all_profiles() -> list[CLIProfile]:
    """Return all registered profiles."""
    return list(_profiles.values())


CLAUDE = CLIProfile(
    name="claude",
    display_name="Claude Code",
    pane_commands=("claude",),
    start_command_pattern=r"\bclaude\b",
    pane_title_pattern=r"Claude Code",
    prompt_re=r"^\s*❯\s*",
    prompt_char="❯",
    response_bullet="●",
    tool_header_re=r"^● \w+\(",
    busy_indicator="esc to interr",
    spinner_re=r"^[^\w\s●❯─━⏵⏸] \w",
    interrupted_pattern="Interrupted",
    compacting_pattern=r"[Cc]ompacting",
    event_map={
        "Stop": "stop",
        "PreToolUse": "pre_tool",
        "Notification": "notification",
    },
    tool_map={
        "Bash": "shell",
        "EnterPlanMode": "plan",
        "AskUserQuestion": "question",
        "Edit": "edit",
        "Write": "write",
        "Read": "read",
    },
    restart_cmd="claude -c",
    launch_cmd="claude",
)

# Gemini CLI UI patterns (discovered from live v0.28.2 session):
#   Idle prompt:  " >   Type your message or @path/to/file"
#   Response:     "✦ Here is my response..."
#   Tool calls:   "╭─── ✓  ReadFile path ───╮" (box drawing)
#   Spinner:      "⠋ Developing the Essay Outline (esc to cancel, 13s)"
#   Decorations:  "▀▀▀" (top bar) / "▄▄▄" (bottom bar) around prompts
#   Status bar:   "~/.../project (branch)  no sandbox  Auto (Gemini 3) /model"
#   pane_title:   "◇  Ready (project)" when idle
#   pane_current_command: "node" (NOT "gemini")
#   pane_start_command: empty — detection uses pane_title fallback
GEMINI = CLIProfile(
    name="gemini",
    display_name="Gemini",
    pane_commands=("node",),
    start_command_pattern=r"\bgemini\b",
    pane_title_pattern=r"◇|Gemini",
    prompt_re=r"^\s*>\s*",
    prompt_char=">",
    response_bullet="✦",
    tool_header_re=r"^[╭│╰]",  # box-drawing tool call blocks
    busy_indicator="esc to cancel",
    spinner_re=r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] ",  # braille spinner chars
    interrupted_pattern="",  # TBD — Gemini interrupt pattern unknown
    compacting_pattern=r"$^",  # Gemini doesn't compact
    event_map={
        "AfterAgent": "stop",
        "BeforeTool": "pre_tool",
        "Notification": "notification",
    },
    tool_map={
        "run_shell_command": "shell",
    },
    restart_cmd="gemini -r latest",
    launch_cmd="gemini",
)

register_profile(CLAUDE)
register_profile(GEMINI)


def identify_cli(pane_command: str, start_command: str = "",
                 pane_title: str = "") -> CLIProfile | None:
    """Identify which CLI is running in a pane.

    Checks pane_current_command first (fast exact match), then falls back
    to start_command regex, then pane_title regex for cases like Gemini
    where pane_current_command is 'node' and pane_start_command may be empty.
    """
    # Exact pane_command match (e.g. "claude")
    for profile in _profiles.values():
        if pane_command in profile.pane_commands:
            # For ambiguous commands like "node", need secondary confirmation
            if pane_command in ("node",):
                if start_command and re.search(profile.start_command_pattern, start_command):
                    return profile
                if pane_title and re.search(profile.pane_title_pattern, pane_title):
                    return profile
                # No confirmation available — skip
                continue
            return profile

    # Fallback: check start_command against all profiles
    if start_command:
        for profile in _profiles.values():
            if re.search(profile.start_command_pattern, start_command):
                return profile

    # Last resort: check pane_title
    if pane_title:
        for profile in _profiles.values():
            if re.search(profile.pane_title_pattern, pane_title):
                return profile

    return None
