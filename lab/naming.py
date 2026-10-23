"""成品的落点与编号：叫什么号、落哪个目录。

放一处的理由和 `lab/paths.py` 一个道理：原图、贴纸、带字成图三站都要回答同一个
问题 ——「这一版落哪儿、叫什么号」。三站各拼一遍文件名，迟早有一站拼错，把
fish-007 的贴纸写成 fish-008，三张图就再也对不上是同一张了。

**不依赖任何三方库，也不挂在引擎那条 import 链上**：落盘规则只认目录和日期，
不需要知道配置从哪来、引擎在哪个端口。加字是纯本地动作（`lettering`），它复用
这里的命名，不该因此把 ComfyUI 客户端拖进来。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

__all__ = [
    "daily_dir",
    "next_serial",
    "output_name",
    "parse_serial",
    "plan_output",
]


def daily_dir(output_dir: Path, today: date | None = None) -> Path:
    """`output/YYYY-MM-DD`。按本地日期分堆，方便当天挑图。"""
    day = today or date.today()
    return Path(output_dir) / day.isoformat()


def parse_serial(path: Path) -> int | None:
    """从 `fish-007.orig.png` 里抠出 7；名字不是这个形状就给 None。

    只认 `fish-` 前缀：`IMG-007.jpg` 这种外来名字不算序号 —— 否则素材图会被
    当成 7 号，去背出来的贴纸直接盖掉当天已有的 fish-007。

    序号是同一个配方条目的 id：原图、贴纸、带字成图共用一个号（靠后缀区分）。
    给一张已经在 output/ 里的图做贴纸，就该沿用它原来那个号。
    """
    stem = Path(path).name.split(".", 1)[0]  # fish-007
    prefix, _, digits = stem.partition("-")
    if prefix != "fish" or not digits.isdigit():
        return None
    return int(digits)


def next_serial(directory: Path) -> int:
    """当天目录里下一个可用序号。

    序号是全天的（不是按阶段分的），因为 `fish-001` 是同一个配方条目的 id：
    同一张图的原图、贴纸、带字成图共享一个序号，靠后缀区分。
    已经存在的最大序号 +1，既不会撞车，也不会把删掉的号回收再利用。
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 1

    # 不按 kind 过滤：fish-001.orig.png 与 fish-001.sticker.png 共享一个序号，
    # 只看同一种会让后续阶段撞 id。
    found = (parse_serial(path) for path in directory.glob("fish-*.png"))
    return max((n for n in found if n is not None), default=0) + 1


def output_name(serial: int, kind: str) -> str:
    """成品叫什么名字。`kind` 是后缀那一段：orig / sticker / captioned。"""
    return f"fish-{serial:03d}.{kind}.png"


def plan_output(
    output_dir: Path, source: Path, today: date | None, kind: str
) -> tuple[Path, int]:
    """这一版落哪儿、叫什么号。`kind` 是文件名里那一段（orig / sticker / captioned）。

    源图就在 output/ 里时贴着它落 —— 贴纸和原图放一起才方便对着看；素材图
    （在素材根里）不往那儿写，落到今天那个目录，按当天最大号接着排。

    序号沿用源图的：fish-007 的原图与贴纸是同一个配方条目的两个面。
    同名成品已经存在就是"把这张重做一遍"，由调用方决定覆盖还是报错。
    """
    output_dir = Path(output_dir)
    folder = (
        source.parent if output_dir in source.parents else daily_dir(output_dir, today)
    )
    serial = parse_serial(source)
    if serial is None:
        serial = next_serial(folder)
    return folder / output_name(serial, kind), serial
