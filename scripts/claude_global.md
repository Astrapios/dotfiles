# Global Claude Code Instructions

## Telegram Integration

When the user asks you to show, send, or share an image, figure, chart, screenshot, or any visual file to Telegram, use:

```bash
tg-hook send-photo /path/to/file.png "optional caption"
```

This sends the file directly to the user's Telegram chat. Use it for:
- Generated plots, charts, or diagrams
- Screenshots or captured images
- Any image file the user wants to see on their phone
