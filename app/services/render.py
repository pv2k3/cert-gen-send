import functools
from typing import Any, Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.fonts import resolve_font_path

DEFAULT_SIZE = 48

# (x, y) is the point the user clicked: the left/centre/right edge of the text,
# vertically centred on y. The UI places markers using the same convention.
ALIGN_ANCHORS = {"left": "lm", "center": "mm", "right": "rm"}


@functools.lru_cache(maxsize=64)
def get_font(font_path: Optional[str], size: int):
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    return ImageFont.load_default(size=size)


def normalize_field(field: Dict[str, Any]) -> Dict[str, Any]:
    """Fills defaults and resolves align/font so DB rows and UI payloads render identically."""
    f = dict(field)
    f["size"] = int(f.get("size") or DEFAULT_SIZE)
    f["color"] = f.get("color") or "#000000"
    f["bold"] = bool(f.get("bold"))
    align = f.get("align") or "center"
    # Accept 'left'/'center'/'right' and raw two-letter Pillow anchors ('ms', 'lm', ...).
    f["anchor"] = ALIGN_ANCHORS.get(align, align if len(align) == 2 else "mm")
    if not f.get("font_file"):
        f["font_file"] = resolve_font_path(f.get("font_family"), f["bold"])
    return f


def wrap_text(text: str, font, max_width: Optional[int]) -> List[str]:
    """Wraps text into lines no wider than max_width pixels."""
    if not max_width or max_width <= 0:
        return [text]

    lines: List[str] = []
    current: List[str] = []
    for word in text.split(" "):
        if font.getlength(" ".join(current + [word])) <= max_width:
            current.append(word)
        elif current:
            lines.append(" ".join(current))
            current = [word]
        else:
            lines.append(word)  # a single word wider than max_width
    if current:
        lines.append(" ".join(current))
    return lines or [text]


def render_certificate(template_path: str, fields: List[Dict[str, Any]], row_data: Dict[str, str]) -> Image.Image:
    img = Image.open(template_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    for raw in fields:
        field = normalize_field(raw)
        text = str(row_data.get(field["column_name"], "") or "")
        if not text:
            continue

        font = get_font(field["font_file"], field["size"])
        lines = wrap_text(text, font, field.get("max_width"))
        ascent, descent = font.getmetrics()
        line_height = (ascent + descent) * 1.2
        # Fake bold: draw with symmetric offsets that scale with the font size.
        stroke = max(1, field["size"] // 40) if field["bold"] else 0

        for i, line in enumerate(lines):
            y = field["y"] + i * line_height
            draw.text(
                (field["x"], y), line, font=font, fill=field["color"],
                anchor=field["anchor"], stroke_width=stroke, stroke_fill=field["color"],
            )

    return img


def export_to_pdf(image: Image.Image, output_path: str):
    """Converts RGBA image to RGB and saves as a single-page PDF."""
    image.convert("RGB").save(output_path, "PDF", resolution=100.0)
