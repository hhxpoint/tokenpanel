"""生成 TokenPanel 应用图标。

设计：暖深色圆角底板 + 三段递增的橙色柱，呼应面板里的用量图表。
全部按 4096px 超采样绘制，再降采样到各档尺寸，保证 16px 下依然清晰。

用法：python assets/make_icon.py
产物：assets/icon.ico, assets/icon.png
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
SS = 4096           # 超采样画布
MASTER = 1024       # 主图尺寸
RADIUS_RATIO = 0.235

BG_TOP = (48, 43, 37)
BG_BOTTOM = (20, 17, 13)
BAR_TOP = (255, 154, 96)
BAR_BOTTOM = (196, 58, 22)


def vertical_gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    """按目标宽高直接生成竖向渐变，避免大图二次缩放。"""
    w, h = size
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
    return strip.resize((w, h), Image.BILINEAR)


def radial(size: int, center: tuple[float, float], radius: float) -> Image.Image:
    """单通道径向渐变，用于左上角柔光。"""
    small = 256
    img = Image.new("L", (small, small), 0)
    draw = ImageDraw.Draw(img)
    cx, cy = center[0] * small, center[1] * small
    r = radius * small
    steps = 64
    for i in range(steps, 0, -1):
        t = i / steps
        draw.ellipse(
            [cx - r * t, cy - r * t, cx + r * t, cy + r * t],
            fill=int(255 * (1 - t) ** 2.1),
        )
    return img.resize((size, size), Image.BICUBIC)


def build_master() -> Image.Image:
    tile = vertical_gradient((SS, SS), BG_TOP, BG_BOTTOM).convert("RGBA")

    # 左上柔光
    glow = radial(SS, (0.24, 0.16), 0.66).point(lambda v: int(v * 0.16))
    light = Image.new("RGBA", (SS, SS), (255, 226, 200, 0))
    light.putalpha(glow)
    tile = Image.alpha_composite(tile, light)

    # 圆角裁切
    mask = Image.new("L", (SS, SS), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SS - 1, SS - 1], radius=int(SS * RADIUS_RATIO), fill=255
    )
    tile.putalpha(mask)

    # 三根柱子：克制的圆角矩形，去掉糖果感的高光
    width, gap = 604, 276
    total = width * 3 + gap * 2
    left = (SS - total) // 2 - int(SS * 0.006)  # 右重，略向左补偿
    bottom = int(SS * 0.795)
    heights = [int(SS * 0.312), int(SS * 0.448), int(SS * 0.578)]
    radius = int(width * 0.30)

    # 柱体后方一层极淡暖光，让主体从深底上浮起来
    warm = radial(SS, (0.5, 0.62), 0.46).point(lambda v: int(v * 0.16))
    warm_layer = Image.new("RGBA", (SS, SS), (255, 138, 80, 0))
    warm_layer.putalpha(warm)
    tile = Image.alpha_composite(tile, warm_layer)

    bars = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    for i, h in enumerate(heights):
        x0 = left + i * (width + gap)
        y0 = bottom - h

        body = vertical_gradient((width, h), BAR_TOP, BAR_BOTTOM)
        bmask = Image.new("L", (width, h), 0)
        ImageDraw.Draw(bmask).rounded_rectangle(
            [0, 0, width - 1, h - 1], radius=radius, fill=255
        )
        layer = Image.new("RGBA", (width, h), (0, 0, 0, 0))
        layer.paste(body, (0, 0), bmask)

        canvas = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
        canvas.paste(layer, (x0, y0))
        bars = Image.alpha_composite(bars, canvas)

    # 主体投影
    sil = Image.new("RGBA", (SS, SS), (0, 0, 0, 255))
    sil.putalpha(bars.split()[3])
    shadow = sil.filter(ImageFilter.GaussianBlur(int(SS * 0.014)))
    shadow.putalpha(shadow.split()[3].point(lambda v: int(v * 0.34)))

    out = Image.alpha_composite(tile, shadow)
    out = Image.alpha_composite(out, bars)

    # 内描边
    rim = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    pad = int(SS * 0.012)
    ImageDraw.Draw(rim).rounded_rectangle(
        [pad, pad, SS - 1 - pad, SS - 1 - pad],
        radius=int(SS * RADIUS_RATIO) - pad,
        outline=(255, 255, 255, 26),
        width=max(2, int(SS * 0.005)),
    )
    out = Image.alpha_composite(out, rim)

    return out.resize((MASTER, MASTER), Image.LANCZOS)


def main() -> None:
    master = build_master()
    master.save(ROOT / "icon.png")

    sizes = [256, 128, 96, 64, 48, 40, 32, 24, 20, 16]
    frames = [master.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[0].save(ROOT / "icon.ico", format="ICO", sizes=[(s, s) for s in sizes])
    print(f"icon.ico / icon.png 已生成：{len(sizes)} 档尺寸，主图 {MASTER}px")


if __name__ == "__main__":
    main()
