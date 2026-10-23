"""素材路径的通用约定。

放一处的理由很实在：`lab doctor` 要数素材、`lab assets` 要扫素材，两边都得知道
「什么算一张图」「哪些目录不算素材」。表分两份，迟早一边加、一边忘。

同一张后缀表还让「喂给引擎的那张图」只需要一份校验：底图复用、去背、加字都是
"给一张本机图片"，该报的三件事（不在了 / 不是文件 / 后缀不支持）一模一样；
去背与加字还都只认静帧，"动图请先挑一帧"这条判据也只写一次。

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
    "check_still_image",
    "iter_image_files",
]

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

#: 流程成品（比对拼图）与回收站，不算素材
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


def check_still_image(
    path: Path | str, *, what: str = "图片", why: str, detail: str = ""
) -> Path:
    """在 check_image_file 之上再要一条：得是静帧，动图请先自己挑一帧。

    去背与加字都只认静帧，且拒绝的理由是同一个 —— 所以判据放一处。"先挑一帧"
    这件事人来做比程序瞎猜第一帧靠谱：动图取哪一帧是审美判断，不是技术判断。

    `detail` 用来补那一站自己的理由（比如去背站说清"引擎只会取第一帧"），
    光说"这一步不做"用户不知道该期待什么。
    """
    source = check_image_file(path, what=what)
    if source.suffix.lower() == ".gif":
        because = f"（{detail}）" if detail else ""
        raise ValueError(f"动图{why}：{source.name}{because} —— 先挑一帧存成 PNG 再来。")
    return source
