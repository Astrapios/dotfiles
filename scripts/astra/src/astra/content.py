"""Noise filtering, response extraction, permission parsing."""
from __future__ import annotations

import difflib
import html as _html
import re

from astra import tmux


# --- Markdown → Telegram HTML rendering (for focus messages) ---------------
# Telegram HTML supports a small tag set: b, i, u, s, code, pre, a, blockquote.
# Converting Claude's Markdown to it (instead of dumping everything in one ```
# block) makes focus messages readable: prose renders, only real code stays
# monospace. HTML escaping is clean (only < > &), and tags are always balanced
# by construction here, so output is valid; the send path still falls back to
# plain text on any 400.

def _render_inline_html(s: str) -> str:
    """Render inline Markdown spans in one line to Telegram HTML."""
    # Protect inline code spans first so their contents aren't mangled.
    codes: list[str] = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", _stash, s)
    s = _html.escape(s)
    # Links [text](url)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               lambda m: f'<a href="{_html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', s)
    # Bold, then italic, then strikethrough. Markers survive html.escape.
    s = re.sub(r"\*\*([^*\n]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", s)
    s = re.sub(r"(?<![_\w])_([^_\n]+)_(?!\w)", r"<i>\1</i>", s)
    s = re.sub(r"~~([^~\n]+)~~", r"<s>\1</s>", s)
    for i, c in enumerate(codes):
        s = s.replace(f"\x00{i}\x00", f"<code>{_html.escape(c)}</code>")
    return s


_TOOL_LINE_RE = re.compile(r"^🔧 (\S+?)\((.*)\)$")

# Distinct colored emoji per tool type so a tool call is recognizable at a
# glance (Telegram message text has no real color; the emoji is the "color").
# Matched case-insensitively on the tool name; MCP names use their suffix.
_TOOL_ICONS = {
    "bash": "💻", "bashoutput": "💻", "killshell": "💻", "killbash": "💻",
    "read": "📖", "edit": "✏️", "multiedit": "✏️", "update": "✏️",
    "write": "🖊️", "notebookedit": "📓",
    "glob": "🗂️", "grep": "🔎", "search": "🔎",
    "webfetch": "🌐", "fetch": "🌐", "websearch": "🌐",
    "task": "🤖", "todowrite": "☑️",
    "enterplanmode": "📋", "exitplanmode": "📋", "askuserquestion": "❓",
    "skill": "⚡", "slashcommand": "⚡",
}


def _tool_icon(name: str) -> str:
    """Pick a distinct emoji for a tool name (falls back to 🔧)."""
    key = name.lower()
    if key in _TOOL_ICONS:
        return _TOOL_ICONS[key]
    # MCP tools render as mcp__server__tool — key off the trailing segment.
    tail = key.rsplit("__", 1)[-1]
    return _TOOL_ICONS.get(tail, "🔧")


def md_to_telegram_html(text: str) -> str:
    """Convert Claude's Markdown-ish text to Telegram HTML.

    Fenced ``` code → <pre>; inline `code` → <code>; **/__ → bold; */_ → italic;
    ~~ → strike; [t](u) → link; #-headings → bold; -/*/+ bullets → •; and the
    ``🔧 Name(args)`` tool-header lines → ``🔧 <b>Name</b> <code>args</code>``.
    """
    out: list[str] = []
    in_fence = False
    fence_buf: list[str] = []
    fence_lang = ""
    for line in text.split("\n"):
        st = line.strip()
        fence = re.match(r"^```(\w*)\s*$", st)
        if fence and not in_fence:
            in_fence, fence_buf, fence_lang = True, [], fence.group(1)
            continue
        if in_fence:
            if st == "```":
                out.append(_render_code_block(chr(10).join(fence_buf), fence_lang))
                in_fence, fence_buf, fence_lang = False, [], ""
            else:
                fence_buf.append(line)
            continue
        tm = _TOOL_LINE_RE.match(line)
        if tm:
            name = _html.escape(tm.group(1))
            arg = _html.escape(tm.group(2))
            icon = _tool_icon(tm.group(1))
            out.append(f"{icon} <b>{name}</b> <code>{arg}</code>" if arg else f"{icon} <b>{name}</b>")
            continue
        h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if h:
            out.append(f"<b>{_render_inline_html(h.group(2))}</b>")
            continue
        lm = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if lm:
            out.append(f"{lm.group(1)}• {_render_inline_html(lm.group(2))}")
            continue
        out.append(_render_inline_html(line))
    if in_fence and fence_buf:  # unterminated fence
        out.append(_render_code_block(chr(10).join(fence_buf), fence_lang))
    return "\n".join(out)


def _render_code_block(code: str, lang: str = "") -> str:
    """Render a fenced code block as Telegram HTML. A language tag uses
    ``<pre><code class="language-x">`` so Telegram shows it as a distinct,
    language-labeled block with a copy button."""
    body = _html.escape(code)
    if lang:
        return f'<pre><code class="language-{_html.escape(lang)}">{body}</code></pre>'
    return f"<pre>{body}</pre>"


_BOX_VERT = set("│║")
_BOX_HORIZ_CORNER = set("┌┐└┘├┤┬┴┼─━╔╗╚╝╠╣╦╩╬")

# Satisfaction survey bullet: looks like a response ● but is UI chrome.
# Checked in bullet-search (clean_pane_content, _has_response_start) and
# noise filtering (_filter_noise, _strip_dialog).
_SURVEY_MARKER = 'How is Claude doing'

# Elapsed-timer fragment that ticks every capture, e.g. "(1m 37s)", "(36s)",
# "(1m 31s · timeout 10m)", "(2m 4s · ↓ 6.1k tokens)". Shared by _filter_noise
# (whole-line filter) and running-tool detection.
_ELAPSED_TIMER_LINE_RE = re.compile(r'^\((?:\d+h\s*)?(?:\d+m\s*)?\d+s\b.*\)$')

# Leading glyphs Claude cycles for the response/tool bullet while a line is
# rendering/animating (settled ●, plus spinner/star frames). A torn repaint
# can also drop the glyph entirely, leaving just indentation.
_BULLET_GLYPHS = "●⏺⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏✶✻✽✳✢✷✻*"
# Header line for a Claude tool call in ANY bullet state — settled, spinner-
# prefixed, or bulletless (torn repaint). Group 1 is the tool name, group 2 the
# remainder from "(" on.
# Group 1 = tool name (allows MCP/dotted/lowercase like ``mcp__srv__tool``),
# group 2 = everything after the opening "(".
_CLAUDE_TOOL_HEADER_RE = re.compile(
    rf'^[{_BULLET_GLYPHS}]?\s*([A-Za-z_][\w.:-]*)\((.*)$')
# Known Claude tool display names. A bulletless "Word(" is only treated as a
# tool header when the name is in this set (avoids swallowing prose like
# "foo(x)"); a ●/spinner-bulleted "Word(" is always a tool call.
_CLAUDE_TOOL_NAMES = frozenset({
    "Bash", "BashOutput", "KillShell", "KillBash", "Read", "Edit", "MultiEdit",
    "Write", "Update", "NotebookEdit", "Glob", "Grep", "Search", "WebFetch",
    "Fetch", "WebSearch", "Task", "TodoWrite", "EnterPlanMode", "ExitPlanMode",
    "AskUserQuestion", "Skill", "SlashCommand",
})


def _match_claude_tool(s: str) -> str | None:
    """If a stripped line is a Claude tool header in any bullet state, return
    its canonical ``Name(args…`` form; else None.

    Recognizes ``● Bash(x)``, spinner-prefixed ``✶ Bash(x)``, and the
    bulletless torn-repaint ``Bash(x)`` (validated against known tool names so
    prose isn't swallowed). This is what makes the diff stable when the leading
    bullet toggles ●↔blank while a tool runs.

    Rejects prose that merely contains parentheses (e.g. ``● Fixed(config). Now
    the rest:``): after the last ``)`` only a tool result (``⎿ …``) or a
    truncation ellipsis may follow — anything else means it is prose, not a
    tool header, and must not be collapsed (which would drop the lines under
    it as tool body)."""
    m = _CLAUDE_TOOL_HEADER_RE.match(s)
    if not m:
        return None
    name, rest = m.group(1), m.group(2)  # rest = text after the "("
    close = rest.rfind(")")
    if close != -1:
        tail = rest[close + 1:].strip()
        if tail and not tail.startswith(("⎿", "…")):
            return None
    had_glyph = bool(s) and s[0] in _BULLET_GLYPHS
    if had_glyph or name in _CLAUDE_TOOL_NAMES:
        return f"{name}({rest}"
    return None


def _canonicalize_lines(lines: list[str]) -> list[str]:
    """Normalize cosmetic per-capture variation that would otherwise churn the
    diff, WITHOUT dropping or merging lines: NBSP→space and rstrip. (Bullet/
    tool-header normalization happens in _collapse_tool_calls; volatile status
    lines are removed by _filter_noise.)"""
    return [ln.replace("\u00a0", " ").rstrip() for ln in lines]


def _is_survey_bullet(s: str) -> bool:
    """Check if a stripped line is the satisfaction survey bullet."""
    return _SURVEY_MARKER in s


def _has_table(text: str) -> bool:
    """Check if text contains an ASCII/Unicode table.

    Requires structural evidence of a table, not just any box-drawing char.
    A single │ at line start is a tree/indent character (tool call output),
    and │ can appear in prose text.  Needs either:
      - A line with 3+ vertical box chars (│ col │ col │) — a real table row
      - A line with horizontal rules + corners (┌──┬──┐, ├──┼──┤)
      - Pipe-delimited rows (| col | col |)
    """
    for line in text.splitlines():
        stripped = line.strip()
        # Box-drawing vertical: 3+ on same line means table row (│ c1 │ c2 │)
        vert_count = sum(1 for ch in stripped if ch in _BOX_VERT)
        if vert_count >= 3:
            return True
        # Horizontal rules with corners/junctions (e.g. ┌──┬──┐)
        if any(ch in _BOX_HORIZ_CORNER for ch in stripped):
            horiz_count = sum(1 for ch in stripped if ch in _BOX_HORIZ_CORNER)
            if horiz_count >= 3:
                return True
        # Pipe-delimited rows: at least 2 pipes on a line with content between them
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


# Footer hints that mark an interactive selection menu awaiting input.
# Distinct from the working indicator ("esc to interrupt"/"esc to interr")
# and from the idle footer ("? for shortcuts · ← for agents").
_MENU_FOOTER_RE = re.compile(
    r'(?i)('
    r'enter to (set|select|confirm|use)'   # /model: "Enter to set as default"
    r'|esc to (cancel|close)'              # most menus
    r'|↑/↓ to navigate|to navigate'        # /agents-style nav line
    r')'
)

# A line offering free-text / search input inside a menu.
_MENU_TEXT_AFFORDANCE_RE = re.compile(
    r'(?i)(type (something|here|your|to)|to search|search\.\.\.|/ to search)'
)

# Option line: optional ❯/●/○ pointer + "N. label". Reused shape from
# _detect_numbered_dialog, but scanned over a larger window because real
# menus (e.g. /model) span well beyond the last 10 lines.
_MENU_OPTION_RE = re.compile(r'^\s*[❯>●○✔\s]*(\d+)\.\s+(.+)')

# The selection cursor on a numbered option (e.g. "  ❯ 1. Yes…"). This is
# the strongest menu signal: every interactive Claude Code menu renders it,
# and ordinary prose numbered lists never do. Some menus (the /model "Switch
# model?" confirmation) have NO footer at all, so we can't rely on the
# footer alone.
_MENU_POINTER_RE = re.compile(r'^\s*[❯>›]\s+\d+\.\s')

# Claude's working indicator — never appears in a real menu; a hard guard
# against treating a mid-work frame as a menu.
_WORKING_RE = re.compile(r'(?i)esc to interr')


def _detect_interactive_menu(raw: str):
    """Detect a tap-to-selectable Claude Code slash-command menu.

    Returns ``(title, options, free_text_index | None)`` or ``None``.

    Slash-command menus fire no hooks (unlike permission/AskUserQuestion
    dialogs), so they're recognized from pane content alone. A menu is a
    numbered option list (≥2) whose top is bounded by a ``────`` separator,
    confirmed by EITHER:
      * the ❯ selection cursor on one of the options (``_MENU_POINTER_RE``)
        — present on every interactive menu, including footer-less ones
        like the /model "Switch model?" confirmation; or
      * a menu-nav footer (``_MENU_FOOTER_RE``).
    Ordinary prose numbered lists have neither, so they return ``None``.

    Numbered / ❯-pointer lists only. Tabbed panels and fuzzy pickers
    (e.g. /agents, /resume) have no numbered options, so they return
    ``None`` (drive manually with /keys).
    """
    lines = raw.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    # Working frame — never a menu.
    if any(_WORKING_RE.search(l) for l in lines):
        return None

    # Scan upward from the last content line collecting numbered options,
    # stopping at the menu's top separator. First label wins per number;
    # wrapped continuation lines (no leading number) are skipped. Track
    # whether any option carries the ❯ selection cursor.
    options: dict[int, str] = {}
    free_text_index: int | None = None
    pointer_seen = False
    top_idx = 0
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if re.match(r'^[─━]{3,}', s):
            top_idx = i
            break
        m = _MENU_OPTION_RE.match(lines[i])
        if m:
            num = int(m.group(1))
            if num not in options:
                options[num] = m.group(2).strip()
            if _MENU_POINTER_RE.match(lines[i]):
                pointer_seen = True
            if _MENU_TEXT_AFFORDANCE_RE.search(s):
                free_text_index = num

    if len(options) < 2:
        return None

    footer_present = any(_MENU_FOOTER_RE.search(l)
                         for l in lines[max(0, len(lines) - 4):])
    if not (pointer_seen or footer_present):
        return None

    # Title: first non-empty, non-separator, non-option line below the
    # separator (e.g. "Select model" / "Switch model?").
    title = ""
    for i in range(top_idx + 1, len(lines)):
        s = lines[i].strip()
        if not s or re.match(r'^[─━]{3,}', s):
            continue
        if _MENU_OPTION_RE.match(lines[i]):
            break
        title = s
        break

    ordered = [options[k] for k in sorted(options)]
    return (title, ordered, free_text_index)


# Markers that identify a TOOL-PERMISSION / approval dialog specifically —
# as opposed to a slash-command menu (/model) or an AskUserQuestion. Used
# by god mode to auto-accept hook-less permission prompts (e.g. editing
# Claude's own settings.json, which fires no PreToolUse/Notification hook).
_PERMISSION_MARKER_RE = re.compile(
    r'(?i)('
    r'do you want to (proceed|make this|run|create|edit)'
    r'|wants to (run|edit|create|read|write|fetch|use)'
    r'|make this edit'
    r'|this command requires approval'
    r'|\bBash command\b|\bEdit file\b|\bCreate file\b'
    r'|\bWrite file\b|\bRead file\b|\bReplace file\b'
    r'|allow Claude to edit its own settings'
    r')'
)

# The approve option of a permission dialog is affirmative ("1. Yes" /
# "Allow ...").
_PERMISSION_AFFIRMATIVE_RE = re.compile(r'(?i)^(yes|allow)\b')


def _detect_permission_dialog(raw: str):
    """Detect a tool-permission/approval dialog awaiting a decision.

    Returns ``(approve_option_number, description)`` or ``None``.

    A permission dialog is a numbered selection menu (per
    ``_detect_interactive_menu``) that ALSO carries a permission marker
    (``_PERMISSION_MARKER_RE``) and whose first option is affirmative
    ("Yes"/"Allow"). This deliberately excludes ``/model`` menus and
    AskUserQuestion prompts, which are user choices — not approvals — and
    must never be auto-accepted by god mode.
    """
    menu = _detect_interactive_menu(raw)
    if not menu:
        return None
    title, options, _free_text = menu
    if not options or not _PERMISSION_AFFIRMATIVE_RE.match(options[0]):
        return None
    if not _PERMISSION_MARKER_RE.search(raw):
        return None
    return (1, title or options[0])


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
            if indent >= 2 and s and not re.match(r'[●•─━❯✻⏵⏸>*\-\d│┃║|┌┐└┘├┤┬┴┼╔╗╚╝╠╣╦╩╬]', s):
                continue
            in_prompt = False
        if re.match(r'^[─━]{3,}$', s):
            continue
        # Status bar: lines that are mostly ─/━ with optional text (e.g. "──── branch-name ──")
        if re.match(r'^[─━]', s) and s.count('─') + s.count('━') > len(s) * 0.4:
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
            if re.match(r'^[^\w\s●❯│┃║] \w', s) and re.search(r'\d+[hms]', s):
                continue
            # Thinking/spinner without timing (e.g. "⠐ Thinking…", "✶ Working…")
            if re.match(r'^[^\w\s●❯│┃║] \w+.*(…|\.\.\.)', s):
                continue
            # Tool output spinner / background-run status that updates in
            # place (e.g. "⎿  Running…", "⎿ Running in the background (↓ to
            # manage)", "⎿ (timeout 10m)"). \s matches the NBSP these lines use.
            if re.match(r'^⎿\s+Running(?:…|\.\.\.| in the background)', s):
                continue
            if re.match(r'^⎿\s+\(timeout\b', s):
                continue
            # Bare elapsed-timer line under a running tool, e.g. "(1m 37s)",
            # "(36s)", "(1m 31s · timeout 10m)", "(2m 4s · ↓ 6.1k tokens)".
            # It ticks every capture, so leaving it in makes focus re-send the
            # whole tool block on every poll. The `●`-bulleted tool line above
            # it is only stripped by _collapse_tool_calls when the bullet is
            # actually captured; on torn repaints it isn't, so filter here too.
            if _ELAPSED_TIMER_LINE_RE.match(s):
                continue
            # Collapsed output: "… +N lines (ctrl+o to expand/see all)"
            if re.match(r'^…\s+\+\d+', s):
                continue
            # Progress ending with ellipsis before ctrl hint: "verb… (ctrl+o to expand)"
            if re.search(r'(?:…|\.\.\.)\s*\(ctrl\+\w to [\w ]+\)\s*$', s):
                continue
        if re.match(r'^\+\d+ more lines \(', s):
            continue
        if 'ctrl+' in s and 'background' in s:
            continue
        # Claude tips: "⎿  Tip: Use /btw to ..."
        if re.match(r'^⎿\s+Tip:', s):
            continue
        # Bare section headers (e.g. "Shell" above a permission dialog)
        if s in ("Shell",):
            continue
        # Satisfaction survey and its rating options
        if _is_survey_bullet(s):
            continue
        if re.match(r'^\d+:\s*(Bad|Fine|Good|Dismiss)', s):
            continue
        filtered.append(line.rstrip())
    return filtered


def _strip_dialog(lines: list[str]) -> list[str]:
    """Strip permission dialog overlay from filtered pane content.

    The dialog appears at the bottom of the pane as a UI overlay containing
    tool descriptions, option lines, and footers. Strip everything from the
    earliest dialog marker (searching the last ~25 lines) to the end.
    """
    _dialog_headers = re.compile(
        r'^(Bash command|Edit file|Create file|Replace file|Read file|'
        r'Do you want to proceed\?|This command requires approval|'
        r'Esc to cancel|Enter to confirm|esc to interrupt|'
        r'How is Claude doing)')
    # Search last 25 lines for the earliest dialog marker
    search_start = max(0, len(lines) - 25)
    cut = len(lines)
    for i in range(search_start, len(lines)):
        s = lines[i].strip()
        if _dialog_headers.match(s):
            # Walk back over any blank/indented preamble lines
            start = i
            while start > search_start and not lines[start - 1].strip():
                start -= 1
            cut = start
            break
    return lines[:cut] if cut < len(lines) else lines


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
            if _is_survey_bullet(s):
                continue
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
        # Find previous prompt (response boundary) to scope the search.
        # This separates the current response from prior ones.
        prev_prompt = -1
        for i in range(end - 1, -1, -1):
            s = lines[i].strip()
            if s.startswith(prompt_char):
                prev_prompt = i
                break
        # Find FIRST text bullet after previous prompt (captures full response
        # including interleaved tool calls, not just the last text section).
        # If no previous prompt found, fall back to LAST text bullet (old
        # behavior) to avoid capturing content from prior responses.
        start = -1
        if bullet:
            if prev_prompt >= 0:
                # Forward search from previous prompt
                for i in range(prev_prompt + 1, end):
                    s = lines[i].strip()
                    if s.startswith(bullet) and not re.match(tool_re, s):
                        if _is_survey_bullet(s):
                            continue
                        start = i
                        break
            else:
                # No previous prompt — backward search for last text bullet
                for i in range(end - 1, -1, -1):
                    s = lines[i].strip()
                    if s.startswith(bullet) and not re.match(tool_re, s):
                        if _is_survey_bullet(s):
                            continue
                        start = i
                        break
        if start < 0:
            # Check if tool bullets exist (response bullet pushed out of range)
            has_tool = any(re.match(tool_re, l.strip()) for l in lines[:end]
                          if l.strip().startswith(bullet)) if bullet else False
            if end < len(lines) and has_tool:
                # Prompt found, tool calls visible but text bullet out of
                # range — fall back to last content lines before prompt
                lines = lines[max(0, end - 30):end]
            else:
                return ""  # No meaningful boundary found
        else:
            lines = lines[start:end]
    filtered = _filter_noise("\n".join(lines), profile=profile)
    if pane_width:
        filtered = tmux._join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def _focus_capture_lines(raw: str, pane_width: int = 0, profile=None) -> list[str]:
    """Clean captured pane content for focus/smartfocus monitoring.

    Shared pipeline used by both focus and smartfocus:
    filter noise → strip prompt lines at end → join wrapped lines.
    Returns a list of cleaned lines suitable for diffing.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    # Strip from the last prompt line to end on RAW lines first,
    # before _filter_noise removes prompt chars (which would leave
    # trailing status bar / chrome lines orphaned).
    raw_lines = raw.splitlines()
    for i in range(len(raw_lines) - 1, -1, -1):
        if raw_lines[i].strip().startswith(prompt_char):
            raw_lines = raw_lines[:i]
            break
    lines = _filter_noise("\n".join(raw_lines), profile=profile)
    if pane_width:
        lines = tmux._join_wrapped_lines(lines, pane_width)
    return lines


def _focus_canonical_lines(raw: str, pane_width: int = 0, profile=None) -> list[str]:
    """Canonical, diff-stable view of a pane for focus/smartfocus.

    Pipeline (filter → collapse/canonicalize → *then* the caller diffs), which
    is the fix for the recurring focus "repeats": cosmetic/animated churn is
    normalized away BEFORE the diff instead of after, so a running tool, a
    toggling bullet, or a ticking timer produces no delta.

      1. strip from the last prompt line to end (content boundary)
      2. _filter_noise (spinners/timers/chrome) + _join_wrapped_lines
      3. _strip_dialog (permission overlay never becomes "new content")
      4. _collapse_tool_calls (every bullet state → one 🔧 header)
      5. _canonicalize_lines (NBSP/rstrip)

    A tool call collapses to a single stable ``🔧 Name(args)`` header whether
    it is running or complete (its body/timer are filtered/collapsed away), so
    it appears exactly once and never churns. Callers store the RESULT as their
    diff baseline (prev_lines), so a stable line compares equal tick-over-tick.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    prompt_char = profile.prompt_char
    raw_lines = raw.splitlines()
    for i in range(len(raw_lines) - 1, -1, -1):
        if raw_lines[i].strip().startswith(prompt_char):
            raw_lines = raw_lines[:i]
            break
    lines = _filter_noise("\n".join(raw_lines), profile=profile)
    if pane_width:
        lines = tmux._join_wrapped_lines(lines, pane_width)
    lines = _strip_dialog(lines)
    lines = _collapse_tool_calls(lines, profile=profile)
    lines = _canonicalize_lines(lines)
    return lines


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


def _collapse_tool_calls(lines: list[str], profile=None) -> list[str]:
    """Collapse tool call sections to single-line headers.

    Replaces tool headers (e.g. ``● Read(file.py)``) with a compact
    ``🔧 Read(file.py)`` line and removes tool body/continuation lines.
    Text bullets pass through unchanged.

    For Gemini, tool blocks use box-drawing characters (``╭``/``│``/``╰``);
    the header line (``╭─ ✓  ToolName ...``) is collapsed and body lines
    (``│``/``╰``) are removed.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    bullet = profile.response_bullet
    is_gemini = profile.name == "gemini"

    collapsed: list[str] = []
    in_tool = False
    for line in lines:
        s = line.strip()
        if is_gemini:
            # Gemini tool blocks: ╭ starts, │/╰ continue
            if s.startswith("╭"):
                # Extract tool name from "╭─ ✓  ToolName args ─╮"
                m = re.search(r'✓\s+(\w+.*?)\s*─*╮?$', s)
                header = m.group(1).strip() if m else s.strip("╭─╮ ")
                collapsed.append(f"🔧 {header}")
                in_tool = True
                continue
            if in_tool and (s.startswith("│") or s.startswith("╰")):
                if s.startswith("╰"):
                    in_tool = False
                continue
            in_tool = False
            collapsed.append(line)
        else:
            # Claude: tool header in ANY bullet state (settled ●, spinner glyph,
            # or bulletless torn repaint) → one canonical "🔧 Name(args)".
            # Matching bulletless headers here (before the diff) is what stops
            # the ●↔blank toggle from churning.
            tool = _match_claude_tool(s)
            if tool is not None:
                collapsed.append(f"🔧 {tool}")
                in_tool = True
                continue
            if s.startswith(bullet) if bullet else False:
                in_tool = False
            if in_tool:
                continue
            collapsed.append(line)
    return collapsed


def _extract_suggestion(pane: str, profile=None) -> str:
    """Extract auto-suggestion text from the idle prompt line.

    Claude Code renders suggestion (ghost) text with the ANSI dim attribute
    (ESC[2m).  We capture with ANSI codes, find the last prompt line
    (skipping UI chrome from the bottom), and check whether the text after
    the prompt char is dim.  If so, we return the plain-text suggestion.
    Returns "" if no prompt, no suggestion, or suggestion is not dim.
    """
    if profile is None:
        from astra import profiles
        profile = profiles.CLAUDE
    raw_ansi = tmux._capture_pane_ansi(pane, 15)
    if not raw_ansi:
        return ""
    prompt_char = profile.prompt_char
    lines = raw_ansi.splitlines()
    # Strip trailing empty lines (after removing ANSI)
    while lines and not re.sub(r'\x1b\[[0-9;]*m', '', lines[-1]).strip():
        lines.pop()
    for i in range(len(lines) - 1, -1, -1):
        plain = re.sub(r'\x1b\[[0-9;]*m', '', lines[i]).strip()
        if not plain or re.match(r'^[─━]{3,}$', plain):
            continue
        if plain.startswith(('⏵⏵ ', '⏸ ')):
            continue
        if re.match(r'^[^\w\s●❯] \w', plain) and re.search(r'\d+[hms]', plain):
            continue
        if re.match(r'^[^\w\s●❯] \w+.*(…|\.\.\.)', plain):
            continue
        if re.search(r'\(ctrl\+\w to \w+\)', plain):
            continue
        plain_full = re.sub(r'\x1b\[[0-9;]*m', '', lines[i])
        m = re.match(rf'^(\s*{re.escape(prompt_char)}\s*)(.*)', plain_full)
        if m:
            # Check for dim attribute (ESC[…2…m) after the prompt char
            ansi_line = lines[i]
            idx = ansi_line.find(prompt_char)
            after = ansi_line[idx + len(prompt_char):] if idx >= 0 else ''
            if not re.search(r'\x1b\[[\d;]*2[\d;]*m', after):
                break  # Text is not dim → typed text or submitted, not suggestion
            suggestion = m.group(2).strip()
            if suggestion and suggestion != prompt_char:
                return suggestion
            break
        break  # First non-chrome, non-prompt line → stop
    return ""


def _compute_new_lines(old_lines: list[str], new_lines: list[str]) -> list[str]:
    """Find genuinely new (inserted/replaced) lines between two captures.

    Returns the new-side lines of every "insert" and "replace" op. Callers
    should use _strip_dialog() before passing to remove ephemeral UI overlays
    that confuse the diff.
    """
    if not old_lines:
        return new_lines
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes = sm.get_opcodes()
    has_changes = any(tag != "equal" for tag, *_ in opcodes)
    if not has_changes:
        return []
    equal_count = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag == "equal")
    if equal_count == 0:
        return new_lines
    new = []
    for tag, _i1, _i2, j1, j2 in opcodes:
        if tag in ("insert", "replace"):
            # Emit ALL new-side lines of the op. A replace's new side is the
            # current version of that region — emitting it in full never drops
            # content. (The old "skip lines also present in the replaced block"
            # dedup silently omitted genuine new lines that happened to match a
            # replaced line — e.g. a repeated `🔧 Bash(cd …)` header — the same
            # hole-punching that sank the 0.36.5 per-line dedup.)
            new.extend(new_lines[j1:j2])
    return new
