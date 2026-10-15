import functools
import os
import platform
from typing import Dict, List, Optional

from PIL import ImageFont

FONT_EXTS = (".ttf", ".otf")


def _search_dirs() -> List[str]:
    system = platform.system()
    if system == "Windows":
        dirs = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
        return dirs
    if system == "Darwin":
        return ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    return ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts")]


@functools.lru_cache(maxsize=2048)
def _font_names(path: str):
    try:
        family, style = ImageFont.truetype(path, 12).getname()
        return family, style or "Regular"
    except Exception:
        return None


def scan_dir(directory: str, source: str) -> List[Dict]:
    fonts = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(FONT_EXTS):
                full = os.path.join(root, file)
                names = _font_names(full)
                if names:
                    fonts.append({"family": names[0], "style": names[1], "path": full, "source": source})
    return fonts


@functools.lru_cache(maxsize=1)
def get_system_fonts() -> List[Dict]:
    """TrueType/OpenType fonts installed on this machine, with real family/style names."""
    fonts: List[Dict] = []
    for d in _search_dirs():
        if os.path.isdir(d):
            fonts.extend(scan_dir(d, "system"))
    fonts.sort(key=lambda f: (f["family"].lower(), f["style"].lower()))
    return fonts


def resolve_font_path(family: Optional[str], bold: bool = False) -> Optional[str]:
    """Finds a font file for a family name, preferring the Bold style when asked."""
    if not family:
        return None
    candidates = [f for f in get_system_fonts() if f["family"].lower() == family.lower()]
    if not candidates:
        return None
    want = "bold" if bold else "regular"
    for f in candidates:
        if f["style"].lower() == want:
            return f["path"]
    return candidates[0]["path"]
