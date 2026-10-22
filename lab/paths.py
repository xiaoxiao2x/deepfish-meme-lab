"""素材路径的通用约定。

放一处的理由很实在：`lab doctor` 要数素材、`lab assets` 要扫素材，两边都得知道
「什么算一张图」「哪些目录不算素材」。表分两份，迟早一边加、一边忘。

这个模块**不依赖任何三方库** —— `lab doctor` 那句「纯标准库」靠它站得住。
（素材索引要读图、要 Pillow，所以那张依赖不能挂在 doctor 的 import 链上。）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

__all__ = ["EXCLUDED_DIRS", "IMAGE_SUFFIXES", "iter_image_files"]

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

#: 流程产物（比对拼图）与回收站，不算素材
EXCLUDED_DIRS = frozenset(
    {"重复检查", ".Trash-0", ".Trash-1000", "$RECYCLE.BIN", ".git"}
)


def iter_image_files(root: Path) -> Iterator[Path]:
    """递归走一个素材根，只吐图片，跳过排除目录（任意层级）。"""
    root = Path(root)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        yield path
