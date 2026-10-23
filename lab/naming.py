"""成品的落点与编号：叫什么号、落哪个目录。

放一处的理由和 `lab/paths.py` 一个道理：原图、贴纸、带字成图三站都要回答同一个
问题 ——「这一版落哪儿、叫什么号」。三站各拼一遍文件名，迟早有一站拼错，把
fish-007 的贴纸写成 fish-008，三张图就再也对不上是同一张了。

**不依赖任何三方库，也不挂在引擎那条 import 链上**：落盘规则只认目录和日期，
不需要知道配置从哪来、引擎在哪个端口。加字是纯本地动作（`lettering`），它复用
这里的命名，不该因此把 ComfyUI 客户端拖进来。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path

__all__ = [
    "daily_dir",
    "entry_id",
    "folder_for",
    "next_serial",
    "output_name",
    "parse_serial",
    "plan_output",
    "stage_of",
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


def stage_of(path: Path) -> str:
    """这张图是哪一站出的：`fish-007.sticker.png` 的 `sticker`。

    文件名是「号.阶段.后缀」，中间那一段就是阶段。**只在这里拆一次** ——
    三站各拆一遍的话，改一次分隔规则就得同时改对三处，改漏一处的那一站
    会把阶段的判断慢慢错开。名字不是这个形状（外来的图）给空串。

    `keep` 靠它对上当天账里那一条，加字站靠它认出"这张已经加过字了"。
    """
    parts = Path(path).name.split(".")
    return parts[-2] if len(parts) >= 3 else ""


def next_serial(directory: Path, *, known: Iterable[int] = ()) -> int:
    """当天目录里下一个可用序号。

    序号是全天的（不是按阶段分的），因为 `fish-001` 是同一个配方条目的 id：
    同一张图的原图、贴纸、带字成图共享一个序号，靠后缀区分。
    已经存在的最大序号 +1，既不会撞车，也不会把删掉的号回收再利用。

    `known` 是"这天的全量记录里已经用过的号"。光看目录不够：图被删掉（废图清走了）
    之后目录里就没有那个号了，下一批又会从它开始，两条不同的生成顶着同一个 id
    躺在两本账里 —— 事后想照着记录重跑，不知道该相信哪一条。传进来的是记录里
    的号，取两者最大值，号就只增不收。
    """
    used = list(known)
    directory = Path(directory)
    if directory.is_dir():
        # 不按 kind 过滤：fish-001.orig.png 与 fish-001.sticker.png 共享一个序号，
        # 只看同一种会让后续阶段撞 id。
        found = (parse_serial(path) for path in directory.glob("fish-*.png"))
        used.extend(n for n in found if n is not None)
    return max(used, default=0) + 1


def entry_id(serial: int) -> str:
    """配方条目的号：`fish-007`。三站共用这个号，靠后缀区分是哪一站出的。"""
    return f"fish-{serial:03d}"


def output_name(serial: int, kind: str) -> str:
    """成品叫什么名字。`kind` 是后缀那一段：orig / sticker / captioned。"""
    return f"{entry_id(serial)}.{kind}.png"


def inside_output(output_dir: Path, path: Path) -> bool:
    """这张图是不是已经躺在 `output/` 里 —— 也就是"它是不是这条链上的一环"。

    两边都 resolve 再比：`output_dir` 是符号链接时（`config._resolve` 只拼不
    resolve），直接比路径会恒假，跨日去背昨天那张图就会落到今天目录、号也跟着重排。
    """
    try:
        return Path(output_dir).resolve() in Path(path).resolve().parents
    except OSError:
        return False


def folder_for(output_dir: Path, source: Path, today: date | None = None) -> Path:
    """这一版落在哪个目录 —— 也就是"记在哪本账里"。

    源图就在 `output/` 里时贴着它落：贴纸和原图放一起才方便对着看，跨日去背
    昨天的图也落在昨天那个目录（那一本账才是它的来路）。素材图（在素材根里）
    不往那儿写，落到今天那个目录。
    """
    if inside_output(output_dir, source):
        return Path(source).parent
    return daily_dir(Path(output_dir), today)


def plan_output(
    output_dir: Path,
    source: Path,
    today: date | None,
    kind: str,
    *,
    known: Iterable[int] = (),
) -> tuple[Path, int]:
    """这一版落哪儿、叫什么号。`kind` 是文件名里那一段（orig / sticker / captioned）。

    源图就在 output/ 里时贴着它落 —— 贴纸和原图放一起才方便对着看；素材图
    （在素材根里）不往那儿写，落到今天那个目录，按当天最大号接着排。

    序号沿用源图的：fish-007 的原图与贴纸是同一个配方条目的两个面。能不能沿用，
    分两种源图：

    - **已经躺在 `output/` 里**（这条链上的一环，跨日去背昨天的图也算）—— 号是它
      在 `output/` 里的身份，跟哪一天无关，一律沿用；
    - **从 `output/` 外面拿进来的**（素材图、把贴纸拷出去再加字）—— 名字里那个号
      未必是它的：外面捡到一张也叫 `fish-007.orig.png` 的图，跟着它落就顶掉当天
      真有的 fish-007，之后按号借参数会借到那条无关生成的 prompt / seed。所以这种
      源图要过两关，任一关不过就换新号：名字不是 `fish-###` 那个形状；这个号
      **账里用过**（`known`），或者**落点目录里已经有同号的别的文件**（账被清走、
      或者根本没读到的时候，只剩目录能作证）。

    `known` 是那本账里已经用过的号，"按最大号接着排"那条路用它：光看目录会撞号，
    账里有 fish-001、图被清走之后目录里没有 001 了，于是新的贴纸又叫 fish-001。
    """
    output_dir = Path(output_dir)
    source = Path(source)
    folder = folder_for(output_dir, source, today)
    kept = parse_serial(source)
    if kept is None:
        serial = next_serial(folder, known=known)
    elif inside_output(output_dir, source):
        # 这条链上的一环：号是它在 output/ 里的身份，跟哪一天、账里记没记过都无关。
        serial = kept
    elif kept in set(known) or kept in _numbers_besides(folder):
        # 外面拿进来的同名图，那个号在这儿是别人的 —— 让开。
        serial = next_serial(folder, known=known)
    else:
        serial = kept
    return folder / output_name(serial, kind), serial


def _numbers_besides(folder: Path) -> set[int]:
    """落点那个目录里已经占着的号。

    只有外来的源图会问这个：账被清走、或者根本没读到的时候，目录里剩下的文件是
    唯一还能作证的东西 —— 那里已经有 fish-007 了，再落一张同号的就把两条无关的
    生成顶成同一个 id。
    """
    if not folder.is_dir():
        return set()
    found = (parse_serial(path) for path in folder.glob("fish-*.png"))
    return {n for n in found if n is not None}
