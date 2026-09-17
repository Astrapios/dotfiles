#!/usr/bin/env python3
"""Join hard-wrapped lines copied out of tmux into one line.

Claude Code wraps its output itself (real newlines plus an indent on each
continuation row), so tmux cannot re-join the rows the way it does for
terminal-wrapped text. This turns the copied rows into a single line:

* ordinary breaks become one space (word wrap);
* a break inside a URL or path becomes nothing. Claude only splits a token
  when it cannot fit, so the row before the split runs to the pane width
  minus its right margin (about 5 columns). Such a row that ends in a URL or
  path token is glued to the first token of the next row.

Usage: tmux_join_lines.py [pane_width] < rows
Bound in .tmux.conf to prefix J (re-copy the last selection joined) and to Y
in copy mode (copy the selection joined).
"""
import re
import sys

MARGIN = 7  # tolerance below the pane width for a "filled" row
TOKEN_RE = re.compile(r"(https?://|s3://|file://|~/|\./|/)[^\s]*$")


def join_lines(text: str, width: int = 0) -> str:
    rows = [r.rstrip() for r in text.split("\n")]
    while rows and not rows[-1]:
        rows.pop()
    out = ""
    prev_raw = ""
    for raw in rows:
        piece = raw.strip()
        if not out:
            out, prev_raw = piece, raw
            continue
        if not piece:
            prev_raw = raw
            continue
        glue = False
        last = out.split()[-1] if out.split() else ""
        if TOKEN_RE.search(last):
            filled = width <= 0 or len(prev_raw) >= width - MARGIN
            if filled and not piece.startswith(("http://", "https://")):
                glue = True
        if glue:
            head, _, rest = piece.partition(" ")
            out += head
            if rest.strip():
                out += " " + rest.strip()
        else:
            out += " " + piece
        prev_raw = raw
    return re.sub(r" {2,}", " ", out).strip()


if __name__ == "__main__":
    w = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 0
    sys.stdout.write(join_lines(sys.stdin.read(), w))
