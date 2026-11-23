# -*- coding: utf-8 -*-
"""make_icon.py — 生成桌面快捷方式用的图标 app.ico（深蓝底 + 金色上行折线）。

用法：
    python scripts/make_icon.py            # 输出 docs/assets/app.ico
    python scripts/make_icon.py out.ico    # 指定输出路径

只依赖 Pillow，纯绘图、不含字体，避免中文/emoji 字体缺失问题。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "assets" / "app.ico"

BG_TOP = (10, 26, 48)
BG_BOTTOM = (18, 52, 92)
GRID = (255, 255, 255, 26)
LINE = (255, 183, 77)
LINE_SOFT = (255, 183, 77, 60)
DOT = (255, 255, 255)
ACCENT = (126, 217, 87)

# 归一化后的折线走势（x, y；y 越大越低）
POINTS = [(0.12, 0.76), (0.28, 0.62), (0.40, 0.70), (0.55, 0.44), (0.68, 0.52), (0.82, 0.26), (0.90, 0.18)]


def _bg(size: int) -> Image.Image:
    """竖向渐变背景 + 圆角遮罩。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel(
            (0, y),
            (
                int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t),
                int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t),
                int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t),
                255,
            ),
        )
    img.paste(grad.resize((size, size)), (0, 0))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255
    )
    img.putalpha(mask)
    return img


def draw_icon(size: int = 256, scale: float = 4) -> Image.Image:
    """先按放大倍数绘制再缩小，得到抗锯齿边缘。"""
    big = size * scale
    img = _bg(big)
    d = ImageDraw.Draw(img, "RGBA")

    # 网格
    step = big / 6
    for i in range(1, 6):
        d.line([(0, i * step), (big, i * step)], fill=GRID, width=max(1, int(big * 0.004)))
    for i in range(1, 6):
        d.line([(i * step, 0), (i * step, big)], fill=GRID, width=max(1, int(big * 0.004)))

    pts = [(x * big, y * big) for x, y in POINTS]

    # 折线下方的半透明填充
    area = pts + [(pts[-1][0], big * 0.88), (pts[0][0], big * 0.88)]
    d.polygon(area, fill=LINE_SOFT)

    # 主折线
    d.line(pts, fill=LINE, width=int(big * 0.055), joint="curve")

    # 末端圆点
    r = big * 0.052
    cx, cy = pts[-1]
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=DOT)

    # 起点小圆点（绿色，表示"起点"）
    r0 = big * 0.032
    x0, y0 = pts[0]
    d.ellipse([x0 - r0, y0 - r0, x0 + r0, y0 + r0], fill=ACCENT)

    return img.resize((size, size), Image.LANCZOS)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    base = draw_icon(256)
    base.save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    png = out.with_suffix(".png")
    base.save(png, format="PNG")
    print(f"已生成 {out}（{out.stat().st_size} 字节）与预览图 {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
