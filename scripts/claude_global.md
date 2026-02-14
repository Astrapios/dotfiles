# Global Claude Code Instructions

## Git Conventions

- Do NOT add "Co-Authored-By" lines or any Claude/AI attribution to commit messages.

## Working Style

- NEVER ask the user to test speculative fixes. Investigate and verify logic yourself first.
- When dealing with terminal UI interactions (tmux send-keys), capture the actual pane content at each step to understand the UI state before writing key sequences.
- Think through the full execution path before making changes — trace through the code, consider timing, and verify assumptions.

## Telegram Integration

When the user asks you to show, send, or share an image, figure, chart, screenshot, or any visual file to Telegram, use:

```bash
tg-hook send-photo /path/to/file.png "optional caption"
```

This sends the file directly to the user's Telegram chat. Use it for:
- Generated plots, charts, or diagrams
- Screenshots or captured images
- Any image file the user wants to see on their phone
