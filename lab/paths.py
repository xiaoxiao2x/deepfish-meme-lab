"""素材路径的通用约定。

放一处的理由很实在：`lab doctor` 要数素材、`lab assets` 要扫素材，两边都得知道
「什么算一张图」「哪些目录不算素材」。表分两份，迟早一边加、一边忘。

同一张后缀表还让「喂给引擎的那张图」只需要一份校验：底图复用与去背都是
"给一张本机图片"，该报的三件事（不在了 / 不是文件 / 后缀不支持）一模一样。

这个模块**不依赖任何三方库** —— `lab doctor` 那句「纯标准库」靠它站得住。
（素材索引要读图、要 Pillow，所以那张依赖不能挂在 doctor 的 import 链上。）
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

__all__ = [
    "EXCLUDED_DIRS",
    "IMAGE_SUFFIXES",
    "check_image_file",
    "iter_image_files",
]

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


def check_image_file(path: Path | str, what: str = "图片") -> Path:
    """确认这是一张能读的图片文件，返回它的绝对路径。

    三条报错各说一件事：文件不在了、给的是目录、后缀不支持。含混的"文件无效"
    会让人在三种原因之间瞎猜。

    返回绝对路径：登记里存的是这张图本身，相对路径一换工作目录就指错地方。
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise ValueError(f"{what}不在：{path} —— 先确认这个文件还在原处。")
    if not path.is_file():
        raise ValueError(f"{what}不是文件：{path} —— 这里要的是单个图片文件。")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        formats = "、".join(sorted(IMAGE_SUFFIXES))
        raise ValueError(
            f"{what}格式不支持：{path}（后缀 {path.suffix or '（没有后缀）'}）。"
            f"支持这些：{formats}"
        )
    return path.resolve()
