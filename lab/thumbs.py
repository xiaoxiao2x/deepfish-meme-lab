"""缩略图：成品库的卡片与素材库的瀑布流用的是同一档宽。

「缩到多宽」只有一个答案。两处各写一份 480，改了一处另一处就悄悄不对，
而 `scripts/check_docs.py` 守不住这种漂 —— 它比的是文档与代码的键集，不是
两个常量相不相等。缩法同样只有一份：只缩不放大，带透明区的照缩、不垫白底
（去背后那张的透明就是它的成品形态，垫上底就不是它了）。

这一层不认账、不认索引、不认页面：给它一个能读的图，它回一段 PNG 字节。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

__all__ = ["WIDTH", "to_png"]

#: 一档宽：240 CSS 像素 × 2 倍屏。卡片与瀑布流格子本来就是一个尺寸。
WIDTH = 480


def to_png(path: Path) -> bytes:
    """把这张缩到那一档，回 PNG 字节。只缩不放大 —— 放大只是把模糊摊开。"""
    from PIL import Image

    try:
        with Image.open(path) as opened:
            image = opened.copy()
    except (OSError, ValueError) as exc:
        raise ValueError(f"这张缩不出来：{Path(path).name} —— {exc}") from None

    if image.width > WIDTH:
        height = max(1, round(image.height * WIDTH / image.width))
        image = image.resize((WIDTH, height), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
