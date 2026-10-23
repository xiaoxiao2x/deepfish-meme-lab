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
from .naming import entry_id, parse_serial, stage_of
from .paths import check_image_file
from .recording import entry_from_base, repo_relative
from .registry import (
    RECORD_NAME,
    Entry,
    library_path,
    load_entries,
    record_path,
    write_entry,
)

__all__ = ["IMPORTED_KIND", "KNOWN_KINDS", "find_entry", "gallery", "keep", "kind_of"]


#: 账里没有来路的那张图，阶段记成这个（docs/design.md §6.2 的第四种）。
#: 留空会在 gallery 的「阶段」列留一个空格子，而且 \"\" 也不在 schema 的枚举里。
IMPORTED_KIND = "imported"


#: 三站落盘时会写进文件名的那几段阶段名（`lab/naming.py` 的
#: `output_name(serial, kind)` 拼出来的中间那一段）。**只认这三个**：拆名字的规则
#: 对任何三段以上的名字都给出一个非空值，而外来的图也叫得出中间那段
#: （`screenshot.2026.10.24.png` 的 `24`、`cover.final.png` 的 `final`）——
#: 照拆名规则一律当真，keep 一张素材图就会往 schema 里写一个没人认识的阶段。
KNOWN_KINDS = frozenset({"orig", "sticker", "captioned"})


def kind_of(path: Path) -> str:
    """这张是哪一站出的：`fish-007.sticker.png` 的 `sticker`。

    keep 要知道这一点，回当天账里才对得上号 —— 拆名字的规则在 `lab/naming.py`，
    三站各写一遍迟早会错开。名字不是这个形状（外来的图）就给空串。

    两道条件都得过：号是 `fish-###` 那个形状，且中间那段是上面三种之一。只看
    中间那段是不行的 —— 外来的图也有中间那段。
    """
    if parse_serial(path) is None:
        return ""
    stage = stage_of(path)
    return stage if stage in KNOWN_KINDS else ""


def _ident(path: Path) -> str:
    """这张图的号。`fish-007.*` 沿用 7；外来的图就用它自己的文件名当号。

    外来图不能给空号：配方库按（号，阶段，日期）去重，一堆空号会互相盖掉，
    最后只剩一条。文件名是它唯一稳定的身份。
    """
    serial = parse_serial(path)
    return entry_id(serial) if serial is not None else Path(path).stem


def _fresh_ident(kept: list[Entry], ident: str, kind: str, where: str) -> str:
    """给没有来路的图挑一个还没被占的号。

    外来图的号是它的文件名（`cover.png` → `cover`），而文件名不是身份：
    同一天从两个目录各留一张 `cover.png`，两条的（号，阶段，日期）一模一样，
    后写的那条会把前一条**静默盖掉** —— 配方库里只剩一张图，人还以为留了两张。
    撞上了就在后面接 `-2`、`-3`；同一张图再留一次（路径没变）不算撞，
    那是幂等的那一条。
    """
    taken = {one.id for one in kept if one.kind == kind and one.file != where}
    if ident not in taken:
        return ident
    suffix = 2
    while f"{ident}-{suffix}" in taken:
        suffix += 1
    return f"{ident}-{suffix}"


def _points_at(cfg: Config, recorded: str, image: Path) -> bool:
    """账里记的那条是不是就是这张图。

    记录里的 file 通常是相对仓库根的（`output/2026-10-24/fish-001.orig.png`），
    也可能是画成绝对路径的旧条目 —— 两种都得认，不然人一换工作目录就 keep 不上。
    """
    path = Path(recorded)
    if recorded.startswith("~"):
        # 记录里的 `~/...` 是"在仓库外但在家目录里"那类（`repo_relative` 折出来的），
        # 原样拼仓库根是拼不出来的，得先展开。
        path = path.expanduser()
    elif not path.is_absolute():
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
    kept = load_entries(library_path(cfg.repo_root))
    entries: list[Entry] = []
    for ledger in _candidate_ledgers(cfg, source, today):
        entries.extend(load_entries(ledger))
    found = find_entry(cfg, entries, source)
    if found is not None:
        # 出生时间与参数都照旧，只把落点改成相对仓库根 —— 从这条起它就是定稿了。
        entry = replace(found, file=repo_relative(cfg, source))
    else:
        # 没有来路的一条：生成那几栏交给 entry_from_base 留空，别在这儿手抄一份 ——
        # 十几个字段各写一遍，改一处漏一处就是两套默认值。
        kind = kind_of(source) or IMPORTED_KIND
        entry = entry_from_base(
            cfg,
            None,
            ident=_fresh_ident(
                kept, _ident(source), kind, repo_relative(cfg, source)
            ),
            path=source,
            kind=kind,
            workflow="",
            model="",
        )
    write_entry(library_path(cfg.repo_root), entry, library=True)
    return entry, found is not None


def gallery(cfg: Config) -> list[Entry]:
    """配方库里的全部条目，最近留的排在最前面。

    倒序而不是按号排：`gallery` 是"我刚留了什么"的视角，不是编号索引。
    """
    entries = load_entries(library_path(cfg.repo_root))
    return sorted(entries, key=lambda one: one.created_at, reverse=True)
