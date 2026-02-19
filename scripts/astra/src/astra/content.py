"""Noise filtering, response extraction, permission parsing."""
from __future__ import annotations

import difflib
import re

from astra import tmux


_TABLE_CHARS = set("│┌┐└┘├┤┬┴┼─━║╔╗╚╝╠╣╦╩╬")


def _has_table(text: str) -> bool:
    """Check if text contains an ASCII/Unicode table.

    Detects box-drawing characters or pipe-delimited rows (| col | col |).
    """
    if any(ch in _TABLE_CHARS for ch in text):
        return True
    # Pipe-delimited rows: at least 2 pipes on a line with content between them
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 3:
            return True
    return False


def _extract_pane_permission(pane: str, profile=None) -> tuple[str, str, list[str], str]:
    """Extract content and options from a permission dialog in a tmux pane.
    Returns (header, content between last dot and options, list of options, context).
    Context is CLI response text (● bullet that isn't a tool call) above the tool bullet.
    Uses progressive capture (30→80→200 lines) to ensure plan content is fully captured.
    profile: CLIProfile to use for pattern matching (defaults to Claude)."""
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    # Progressive capture: try increasing sizes
    lines = []
    options = []
    first_opt_idx = 0
    start = 0
    for num_lines in (30, 80, 200):
        raw = tmux._capture_pane(pane, num_lines)
        if not raw:
            return "", "", [], ""
        lines = raw.splitlines()

        # Find options from last 8 lines only
        options = []
        for line in lines[-8:]:
            m = re.match(r'^\s*[❯>]?\s*(\d+\.\s+.+)', line)
            if m:
                options.append(m.group(1).strip())

        # Find the first option line index in full list
        first_opt_idx = len(lines)
        for i in range(len(lines) - 8, len(lines)):
            if i >= 0 and re.match(r'^\s*[❯>]?\s*\d+\.\s+', lines[i]):
                first_opt_idx = i
                break

        # Find last ● above the options (tool bullet)
        start = 0
        for i in range(first_opt_idx - 1, -1, -1):
            if lines[i].strip().startswith("●"):
                start = i
                break

        # Find response bullet above the tool bullet
        ctx_start = start
        for i in range(start - 1, -1, -1):
            s = lines[i].strip()
            if s.startswith("●") and not re.match(r'^● \w+\(', s):
                ctx_start = i
                break

        # If tool ● or response ● is in the first 3 lines, we likely need more context
        if min(start, ctx_start) <= 2 and num_lines < 200:
            continue
        break

    # Extract response context (between response bullet and tool bullet)
    context_lines = []
    for line in lines[ctx_start:start]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("●"):
            context_lines.append(s[1:].strip())
        else:
            context_lines.append(s)
    context = "\n".join(context_lines).strip()

    # Extract tool + file from ● header (e.g. "● Update(scripts/astra)")
    header = ""
    hdr_file = ""
    for line in lines[start:first_opt_idx]:
        s = line.strip()
        m_hdr = re.match(r'^● (\w+)\((.+?)\)', s)
        if m_hdr:
            header = f"wants to {m_hdr.group(1).lower()} `{m_hdr.group(2)}`"
            hdr_file = m_hdr.group(2)
            break

    # Clean: skip ● header, separators, chrome; dedent diff
    cleaned = []
    for line in lines[start:first_opt_idx]:
        s = line.strip()
        if s.startswith("●"):
            continue
        if re.match(r'^[─━╌]{3,}$', s):
            continue
        if s.startswith(("⎿", "Do you want", "Claude wants")):
            continue
        if s in ("Edit file", "Write file", "Create file", "Fetch", "Bash command"):
            continue
        if hdr_file and s in (hdr_file, hdr_file.rsplit("/", 1)[-1]):
            continue
        m_diff = re.match(r'^\s*\d+\s*([+-])(.*)', line)
        m_ctx = re.match(r'^\s*\d+\s+(.*)', line)
        if m_diff:
            cleaned.append(f"{m_diff.group(1)}{m_diff.group(2)}")
        elif m_ctx:
            cleaned.append(f" {m_ctx.group(1)}")
        elif re.match(r'^\s*\d+\s*$', line):
            cleaned.append("")
        else:
            cleaned.append(line.strip())
    body = "\n".join(cleaned).strip()
    return header, body, options, context


def _detect_numbered_dialog(raw: str) -> tuple[str, list[str]] | None:
    """Detect a numbered-option dialog in pane content (e.g. Gemini trust dialog).

    Returns (question_text, [option_labels]) or None.
    Only matches when options appear in the bottom portion of the pane
    and are NOT inside a tool-call box (which uses ✓ ToolName format).
    """
    lines = raw.splitlines()
    if not lines:
        return None

    # Look for numbered options in the last 10 lines
    # Strip box-drawing borders (│) before matching
    options: list[str] = []
    for line in lines[-10:]:
        stripped = re.sub(r'^[│┃]\s*', '', line).rstrip()
        stripped = re.sub(r'\s*[│┃]$', '', stripped)
        m = re.match(r'^\s*[❯>●○\s]*(\d+)\.\s+(.+)', stripped)
        if m:
            options.append(m.group(2).strip())

    if not options:
        return None

    # Discriminator: tool-call boxes use "✓  ToolName" format, not numbered options
    # Also skip if we only see a single option (likely not a dialog)
    if len(options) < 2:
        return None

    # Extract question text: look for non-option text above the options
    question = ""
    for line in reversed(lines[:-10] if len(lines) > 10 else lines):
        s = line.strip()
        # Skip box drawing, empty lines, UI chrome
        if not s or re.match(r'^[╭╰│─┊┃▀▄]{1,}', s):
            continue
        # Skip the option lines themselves
        if re.match(r'^\s*[❯>●○\s]*\d+\.\s+', s):
            continue
        # Skip status/chrome
        if re.match(r'^[▀▄]{3,}$', s) or 'no sandbox' in s:
            continue
        question = s
        break

    return (question, options)


def _filter_noise(raw: str, keep_status: bool = False, profile=None) -> list[str]:
    """Filter common UI noise from captured pane content.
    If keep_status=True, preserves thinking/spinner status lines.
    profile: CLIProfile to use for pattern matching (defaults to Claude)."""
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    lines = raw.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    filtered = []
    in_prompt = False
    for line in lines:
        s = line.strip()
        # Prompt lines: keep in status mode (context), strip in response mode
        if s.startswith(prompt_char):
            if keep_status:
                in_prompt = False
                filtered.append(line.rstrip())
            else:
                in_prompt = True
            continue
        # Skip wrapped continuations of a filtered prompt line
        if in_prompt:
            indent = len(line) - len(line.lstrip())
            if indent >= 2 and s and not re.match(r'[●•─━❯✻⏵⏸>*\-\d]', s):
                continue
            in_prompt = False
        if re.match(r'^[─━]{3,}$', s):
            continue
        if s.startswith(("⏵⏵ ", "⏸ ")):
            continue
        if s.startswith("Context left until auto-compact:"):
            continue
        if not keep_status:
            if s in ("⏳ Working...", "* Working..."):
                continue
            if re.match(r'^✻ \w+ for ', s):
                continue
            if re.match(r'^[^\w\s] \w', s) and re.search(r'\d+[hms]', s):
                continue
            # Thinking/spinner without timing (e.g. "⠐ Thinking…", "✶ Working…")
            if re.match(r'^[^\w\s●❯] \w+.*(…|\.\.\.)', s):
                continue
            # Tool progress lines (e.g. "Reading 1 file… (ctrl+o to expand)")
            if re.search(r'\(ctrl\+\w to \w+\)', s):
                continue
        if re.match(r'^\+\d+ more lines \(', s):
            continue
        if s.startswith('ctrl+') and 'background' in s:
            continue
        filtered.append(line.rstrip())
    return filtered


def _has_response_start(raw: str, profile=None) -> bool:
    """Check if captured pane content contains the response bullet that starts a response."""
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    bullet = profile.response_bullet
    tool_re = profile.tool_header_re
    if not bullet:
        return False  # Gemini TBD — no known bullet
    lines = raw.splitlines()
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith(prompt_char):
            end = i
            break
    for i in range(end - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith(bullet) and not re.match(tool_re, s):
            return True
    return False


def _detect_interrupted(raw: str, profile=None) -> bool:
    """Check if pane content shows CLI was interrupted (Esc pressed mid-response).

    Looks for the interrupted pattern between the last response and the prompt.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    pattern = profile.interrupted_pattern
    if not pattern:
        return False
    lines = raw.splitlines()
    # Walk backward from end, find last prompt
    end = -1
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith(prompt_char):
            end = i
            break
    if end < 0:
        return False
    # Check the few lines just above prompt for the interrupted marker
    for i in range(end - 1, max(end - 6, -1), -1):
        if pattern in lines[i] and "·" in lines[i]:
            return True
    return False


def _detect_compacting(raw: str, profile=None) -> bool:
    """Check if pane content shows CLI is auto-compacting context.

    Looks for compacting pattern in status/spinner lines.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    pattern = profile.compacting_pattern
    if not pattern or pattern == r"$^":
        return False
    for line in raw.splitlines():
        if re.search(pattern, line):
            return True
    return False


def clean_pane_content(raw: str, event: str, pane_width: int = 0, profile=None) -> str:
    """Clean captured tmux pane content."""
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    bullet = profile.response_bullet
    tool_re = profile.tool_header_re
    lines = raw.splitlines()
    if event == "stop":
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith(prompt_char):
                end = i
                break
        start = -1
        if bullet:
            for i in range(end - 1, -1, -1):
                s = lines[i].strip()
                if s.startswith(bullet) and not re.match(tool_re, s):
                    start = i
                    break
        if start < 0:
            return ""  # No response boundary found
        lines = lines[start:end]
    filtered = _filter_noise("\n".join(lines), profile=profile)
    if pane_width:
        filtered = tmux._join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def clean_pane_status(raw: str, pane_width: int = 0, profile=None) -> str:
    """Clean captured pane content for /status display."""
    filtered = _filter_noise(raw, keep_status=True, profile=profile)
    if pane_width:
        filtered = tmux._join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def _filter_tool_calls(lines: list[str]) -> list[str]:
    """Remove tool call bullets and their continuation lines.

    Tool call bullets match '● Word(' (e.g. '● Bash(command)').
    Everything after a tool bullet until the next text bullet is removed.
    """
    filtered = []
    in_tool = False
    for line in lines:
        s = line.strip()
        if re.match(r'^● \w+\(', s):
            in_tool = True
            continue
        if s.startswith("●"):
            in_tool = False
        if in_tool:
            continue
        filtered.append(line)
    return filtered


def _compute_new_lines(old_lines: list[str], new_lines: list[str]) -> list[str]:
    """Find genuinely new (inserted) lines between two captures."""
    if not old_lines:
        return new_lines
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes = sm.get_opcodes()
    # No actual changes — content is identical or only has replacements
    has_changes = any(tag != "equal" for tag, *_ in opcodes)
    if not has_changes:
        return []
    equal_count = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag == "equal")
    if equal_count == 0:
        return new_lines
    new = []
    for tag, _i1, _i2, j1, j2 in opcodes:
        if tag == "insert":
            new.extend(new_lines[j1:j2])
    return new
