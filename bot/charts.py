"""Daily revenue bar chart rendered with Pillow (PNG bytes for Telegram)."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

SURFACE = "#fcfcfb"
BAR = "#2a78d6"
GRID = "#e4e3df"
INK = "#0b0b0b"
MUTED = "#52514e"


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow without scalable default font
        return ImageFont.load_default()


def _nice_max(v: float) -> float:
    if v <= 0:
        return 10
    for step in (1, 2, 5, 10, 15, 20, 25, 50, 75, 100, 150, 200, 250, 500, 750, 1000, 1500, 2000, 2500, 5000, 7500, 10000, 15000, 20000, 25000, 50000, 100000):
        if v <= step * 4:
            return step * 4
    return v * 1.1


def revenue_chart(days: list[tuple[str, float]], title: str) -> bytes:
    w, h = 1200, 640
    left, right, top, bottom = 110, 40, 110, 70
    img = Image.new("RGB", (w, h), SURFACE)
    d = ImageDraw.Draw(img)
    f_title, f_axis, f_label = _font(34), _font(22), _font(22)

    d.text((left, 34), title, fill=INK, font=f_title)
    vmax = _nice_max(max((v for _, v in days), default=0))
    ph, pw = h - top - bottom, w - left - right

    for i in range(5):  # recessive horizontal grid + y labels
        y = top + ph - ph * i / 4
        d.line([(left, y), (w - right, y)], fill=GRID, width=2 if i == 0 else 1)
        label = f"${vmax * i / 4:,.0f}"
        tw = d.textlength(label, font=f_axis)
        d.text((left - 14 - tw, y - 12), label, fill=MUTED, font=f_axis)

    n = len(days)
    slot = pw / max(n, 1)
    bw = max(4, slot - 6)  # gap between adjacent bars
    peak = max(range(n), key=lambda i: days[i][1]) if n else None
    for i, (label, v) in enumerate(days):
        x0 = left + i * slot + (slot - bw) / 2
        if v > 0:
            bh = max(3, ph * v / vmax)
            y0 = top + ph - bh
            d.rounded_rectangle([x0, y0, x0 + bw, top + ph], radius=min(8, bw / 2), fill=BAR,
                                corners=(True, True, False, False))
            if i == peak:  # single direct label on the best day
                txt = f"${v:,.0f}"
                tw = d.textlength(txt, font=f_label)
                d.text((x0 + bw / 2 - tw / 2, y0 - 30), txt, fill=INK, font=f_label)
        if i % 5 == 0 or i == n - 1:
            tw = d.textlength(label, font=f_axis)
            d.text((x0 + bw / 2 - tw / 2, top + ph + 14), label, fill=MUTED, font=f_axis)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
