"""Draws caption / label / watermark images with Pillow (they get overlaid by ffmpeg)."""

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font_path(setting="auto"):
    if setting and setting != "auto":
        return str(config.resolve(setting))
    custom = sorted(config.resolve("fonts").glob("*.[ot]tf"))
    if custom:
        return str(custom[0])
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _font(path, size):
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _fit(draw, text, path, max_width, start_size, max_lines):
    """Largest font size where the wrapped text fits in max_width and max_lines."""
    size = start_size
    while size > 28:
        font = _font(path, size)
        avg = draw.textlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ", font=font) / 26
        lines = textwrap.wrap(text, width=max(4, int(max_width / avg)))
        if len(lines) <= max_lines and all(draw.textlength(l, font=font) <= max_width for l in lines):
            return font, lines
        size -= 4
    font = _font(path, size)
    return font, textwrap.wrap(text, width=18)[:max_lines]


def _draw_block(img, text, font_file, center_y, start_size, fill, max_lines=3, stroke_ratio=0.09):
    draw = ImageDraw.Draw(img)
    width = img.width
    font, lines = _fit(draw, text, font_file, width * 0.88, start_size, max_lines)
    stroke = max(3, int(font.size * stroke_ratio))
    line_h = int(font.size * 1.12)
    y = center_y - line_h * len(lines) / 2
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (width - w) / 2
        # Soft drop shadow, then the outlined text.
        draw.text((x + 5, y + 7), line, font=font, fill=(0, 0, 0, 140), stroke_width=stroke,
                  stroke_fill=(0, 0, 0, 140))
        draw.text((x, y), line, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += line_h


def caption_image(text, size, layout, font_file, watermark=""):
    """Full-frame transparent PNG with the caption on top (and optional watermark)."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    if layout == "vertical":
        center_y, start = int(h * 0.17), 96
    else:
        center_y, start = int(h * 0.12), 72
    _draw_block(img, text.upper(), font_file, center_y, start, (255, 255, 255, 255))
    if watermark:
        draw = ImageDraw.Draw(img)
        font = _font(font_file, int(h * 0.024))
        tw = draw.textlength(watermark, font=font)
        y = h * (0.9 if layout == "vertical" else 0.92)
        draw.text(((w - tw) / 2, y), watermark, font=font, fill=(255, 255, 255, 190),
                  stroke_width=2, stroke_fill=(0, 0, 0, 190))
    return img


def label_image(label, size, layout, font_file):
    """Big yellow stamp ("EPIC SAVE!") shown right at the moment of the play."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    center_y = int(h * (0.79 if layout == "vertical" else 0.8))
    _draw_block(img, f"{label}!", font_file, center_y, 120 if layout == "vertical" else 96,
                (255, 212, 0, 255), max_lines=2, stroke_ratio=0.1)
    return img
