#!/usr/bin/env python
"""Render a text file as a PNG image with monospace font on dark background."""
import sys

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_SIZE = 18
PAD = 20
BG = (30, 30, 30)
FG = (212, 212, 212)


def render(input_path: str, output_path: str):
    with open(input_path) as f:
        text = f.read()

    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    lines = text.splitlines() or [""]

    # Measure character dimensions using a sample
    bbox = font.getbbox("M")
    char_w = bbox[2] - bbox[0]
    line_h = int((bbox[3] - bbox[1]) * 1.4)

    max_cols = max(len(line) for line in lines)
    img_w = max_cols * char_w + PAD * 2
    img_h = len(lines) * line_h + PAD * 2

    img = Image.new("RGB", (img_w, img_h), BG)
    draw = ImageDraw.Draw(img)

    for i, line in enumerate(lines):
        draw.text((PAD, PAD + i * line_h), line, font=font, fill=FG)

    img.save(output_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} input.txt output.png", file=sys.stderr)
        sys.exit(1)
    render(sys.argv[1], sys.argv[2])
