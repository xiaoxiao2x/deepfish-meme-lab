"""把一张定稿从当天的全量记录搬进配方库（票 #11，ADR 0003）。

「保留」是个**动作**，不是藏在文件属性里的一个状态 —— 一张图被人挑中、写进
`data/registry.json`，那一刻它才算定稿；在那之前它只是当天记录里的一条。
这个模块管的就是这一步：拿一张图，回到它当天那本账里找到它那一条，抄进配方库。

**为什么非要回账里找，而不是就地编一条**：配方库值得进 git 的唯一理由是
「什么参数能出什么图」。种子、步数、模型这些只有生成那一刻知道，事后看图片文件
是看不出来的。所以 keep 的第一件事是照着图找回它那一条，而不是重新描述这张图。

**找不到那一条时也不假装**：图是从别处拿来的（外来的原图去背后直接手工留），
当天账里根本没有它的来路。这时候记下这张图本身的事实（号、阶段、尺寸、时间），
生成那几栏留空 —— 比编一个种子出来诚实，而且 keep 会当场说清楚这条没有来路。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from .config import Config
from .naming import parse_serial, stage_of
from .paths import check_image_file
from .recording import UNKNOWN_VERSION, image_size, repo_relative
from .registry import (
    RECORD_NAME,
    Entry,
    library_path,
    load_entries,
    now_iso,
    record_path,
    write_entry,
)

__all__ = ["IMPORTED_KIND", "find_entry", "gallery", "keep", "kind_of"]


#: 账里没有来路的那张图，阶段记成这个（docs/design.md §6.2 的第四种）。
#: 留空会在 gallery 的「阶段」列留一个空格子，而且 \"\" 也不在 schema 的枚举里。
IMPORTED_KIND = "imported"


def kind_of(path: Path) -> str:
    """这张是哪一站出的：`fish-007.sticker.png` 的 `sticker`。

    keep 要知道这一点，回当天账里才对得上号 —— 拆名字的规则在 `lab/naming.py`，
    三站各写一遍迟早会错开。名字不是这个形状（外来的图）就给空串。
    """
    return stage_of(path)


def _ident(path: Path) -> str:
    """这张图的号。`fish-007.*` 沿用 7；外来的图就用它自己的文件名当号。

    外来图不能给空号：配方库按（号，阶段）去重，一堆空号会互相盖掉，
    最后只剩一条。文件名是它唯一稳定的身份。
    """
    serial = parse_serial(path)
    return f"fish-{serial:03d}" if serial is not None else Path(path).stem


def _points_at(cfg: Config, recorded: str, image: Path) -> bool:
    """账里记的那条是不是就是这张图。

    记录里的 file 通常是相对仓库根的（`output/2026-10-24/fish-001.orig.png`），
    也可能是画成绝对路径的旧条目 —— 两种都得认，不然人一换工作目录就 keep 不上。
    """
    path = Path(recorded)
    if not path.is_absolute():
        path = Path(cfg.repo_root) / path
    try:
        return path.resolve() == image.resolve()
    except OSError:
        return False


def find_entry(cfg: Config, entries: list[Entry], image: Path) -> Entry | None:
    """在当天的账里找这张图那一条。先认路径，再退到（号，阶段）。

    退一步那半截是给"图挪过位置"留的余地：人把图从 output/ 挪进自己的收藏夹
    之后再来 keep，路径对不上了，但号还在文件名里，照样能找回参数。
    """
    image = Path(image)
    for one in entries:
        if _points_at(cfg, one.file, image):
            return one
    ident = _ident(image)
    kind = kind_of(image)
    for one in entries:
        if one.id == ident and (not kind or one.kind == kind):
            return one
    return None


def _candidate_ledgers(cfg: Config, image: Path, today: date | None) -> list[Path]:
    """该去翻哪几本账：图所在那个目录的那本，加上今天那本。

    第一本管"就在原地 keep"，第二本管"图被挪进收藏夹了再 keep" —— 那之后
    图旁边已经没有账了，但当天那本还躺在 output/ 里。两本都要翻，少一本就有
    一半的情况找不到来路。
    """
    paths = [Path(image).parent / RECORD_NAME, record_path(cfg.paths.output_dir, today)]
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def keep(
    cfg: Config, image: Path | str, *, today: date | None = None
) -> tuple[Entry, bool]:
    """把这张图写进配方库，返回（条目，当天账里有没有它的来路）。

    第二项是给终端说实话用的：有来路就是"搬过去一条配方"，没有就只能记下
    这张图本身。返回值不吞掉这个差别，界面才不会把两件事说成一件。
    """
    source = check_image_file(image, what="要保留的图")
    entries: list[Entry] = []
    for ledger in _candidate_ledgers(cfg, source, today):
        entries.extend(load_entries(ledger))
    found = find_entry(cfg, entries, source)
    if found is not None:
        # 出生时间与参数都照旧，只把落点改成相对仓库根 —— 从这条起它就是定稿了。
        entry = replace(found, file=repo_relative(cfg, source))
    else:
        width, height = image_size(source)
        entry = Entry(
            id=_ident(source),
            created_at=now_iso(),
            kind=kind_of(source) or IMPORTED_KIND,
            file=repo_relative(cfg, source),
            width=width,
            height=height,
            workflow="",
            model="",
            prompt="",
            negative="",
            seed=0,
            steps=0,
            cfg=0.0,
            comfyui_version=UNKNOWN_VERSION,
        )
    write_entry(library_path(cfg.repo_root), entry, library=True)
    return entry, found is not None


def gallery(cfg: Config) -> list[Entry]:
    """配方库里的全部条目，最近留的排在最前面。

    倒序而不是按号排：`gallery` 是"我刚留了什么"的视角，不是编号索引。
    """
    entries = load_entries(library_path(cfg.repo_root))
    return sorted(entries, key=lambda one: one.created_at, reverse=True)
