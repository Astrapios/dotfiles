"""Noise filtering, response extraction, permission parsing."""
import difflib
import re

from tg_hook import tmux


def _extract_pane_permission(pane: str) -> tuple[str, str, list[str], str]:
    """Extract content and options from a permission dialog in a tmux pane.
    Returns (header, content between last dot and options, list of options, context).
    Context is Claude's response text (● bullet that isn't a tool call) above the tool bullet.
    Uses progressive capture (30→80→200 lines) to ensure plan content is fully captured."""
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

    # Extract tool + file from ● header (e.g. "● Update(scripts/tg-hook)")
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


def _filter_noise(raw: str, keep_status: bool = False) -> list[str]:
    """Filter common UI noise from captured pane content.
    If keep_status=True, preserves thinking/spinner status lines."""
    lines = raw.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    filtered = []
    for line in lines:
        s = line.strip()
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
            if re.match(r'^[^\w\s●❯] \w+.*…', s):
                continue
        if re.match(r'^\+\d+ more lines \(', s):
            continue
        if s.startswith('ctrl+') and 'background' in s:
            continue
        filtered.append(line.rstrip())
    return filtered


def _has_response_start(raw: str) -> bool:
    """Check if captured pane content contains the ● text bullet that starts a response."""
    lines = raw.splitlines()
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith("❯"):
            end = i
            break
    for i in range(end - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("●") and not re.match(r'^● \w+\(', s):
            return True
    return False


def clean_pane_content(raw: str, event: str, pane_width: int = 0) -> str:
    """Clean captured tmux pane content."""
    lines = raw.splitlines()
    if event == "stop":
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("❯"):
                end = i
                break
        start = 0
        for i in range(end - 1, -1, -1):
            s = lines[i].strip()
            if s.startswith("●") and not re.match(r'^● \w+\(', s):
                start = i
                break
        lines = lines[start:end]
    filtered = _filter_noise("\n".join(lines))
    if pane_width:
        filtered = tmux._join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def clean_pane_status(raw: str, pane_width: int = 0) -> str:
    """Clean captured pane content for /status display."""
    filtered = _filter_noise(raw, keep_status=True)
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
    equal_count = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag == "equal")
    if equal_count < 3:
        return new_lines
    new = []
    for tag, _i1, _i2, j1, j2 in opcodes:
        if tag == "insert":
            new.extend(new_lines[j1:j2])
    return new
