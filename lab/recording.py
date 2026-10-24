"""把一次产出翻成当天全量记录里的一条（票 #11，ADR 0003）。

三站都要往当天那本账里落一笔 —— 出片、去背、加字。而"一条记录长什么样"只该
写一次：三处各自拼字典的话，字段名与默认值迟早对不上，读账的人就得记三套。

翻译，外加借上一步的两条规则：

- 量宽高、把路径改成相对仓库根（仓库挪整个位置，账还读得懂）；
- 把可复现的那几个字段从上一步的条目里带过来 —— 贴纸的 prompt 和种子不是它
  自己的，是它那张原图的；加字那一步更是纯 Pillow，连引擎都没碰过；
- 引擎版本取不到就写「未知」，不为了问一句版本号把已经出成的图判成失败。

「借」这件事有两道规则得住在这儿，不能交给调用方各自判断：`find_base` 只认同号
那条 `orig`（同号只剩贴纸时宁可不借，理由见函数注释），`_borrow_version` 只在
这一步真的问不到版本时才往上一条借。两处都是判断而不是搬运 —— 放调用方手里，
三个站迟早各写一套，账里就会出现三种借法。

真正读写文件的是 `lab/registry.py`：两本账的形状与落盘规则都在那儿。
"""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .naming import parse_serial
from .registry import Entry, EntrySource, now_iso
from .workflows import GenSpec

#: 出片那一站的阶段名。写在这儿是因为 `find_base` 要按它挑"来路那一条"，
#: 而 recording 是三个站共用的翻译层 —— 各自 import 一份常量会转成环。
IMAGE_KIND = "orig"

__all__ = [
    "IMAGE_KIND",
    "UNKNOWN_VERSION",
    "engine_version",
    "entry_for_gen",
    "entry_from_base",
    "find_base",
    "image_size",
    "points_at",
    "repo_relative",
    "used_serials",
]


#: 版本取不到时写下的字。它同时是「这次没问到」与「借上一条的」两条规则的开关：
#: 一个"未知"盖掉上一步真记下来的版本，等于把复现最需要的那一栏抹了。
UNKNOWN_VERSION = "未知"


def image_size(path: Path) -> tuple[int, int]:
    """量一下图的宽高。记录里要用，回执里也省得再开一次图。

    Pillow 只在函数体内 import：`lab/registry` 是纯标准库，读账这件事不该为了
    量一张图就被拖上重依赖。
    """
    from PIL import Image

    with Image.open(path) as opened:
        return opened.size


def engine_version(engine: object) -> str:
    """这次用的引擎版本。取不到就写「未知」。

    连不上时 `EngineClient.version()` 会抛 `EngineUnreachable`；这里的取舍是：
    为了问一句版本号把已经出成的图判成失败，不划算。
    """
    from .comfy import EngineError

    getter = getattr(engine, "version", None)
    if getter is None:
        return UNKNOWN_VERSION
    try:
        return str(getter())
    except EngineError:
        return UNKNOWN_VERSION


def repo_relative(cfg: Config, path: Path) -> str:
    """记录里的路径：相对仓库根；仓库外就折成 `~/...`。

    `data/registry.json` 是要进 git 的（ADR 0003），把 `/home/<你的用户名>/...`
    写进去等于把本机布局带进版本库 —— docs/design.md §4 那条「本机绝对路径不进
    仓库」说的就是这件事。折成 `~/收藏/x.png` 既不带用户名，读的人也知道它在家目录。
    连家目录都不是（挂在 /mnt 上之类）才退回绝对路径：那种情况下"在哪儿"是这条
    记录唯一还能提供的线索。
    """
    target = Path(path).resolve()
    for base, prefix in ((cfg.repo_root, ""), (Path.home(), "~/")):
        try:
            return prefix + str(target.relative_to(Path(base).resolve()))
        except ValueError:
            continue
    return str(target)


def used_serials(entries: list[Entry]) -> list[int]:
    """这本账里已经用过的号。

    给下一批生成挑号用：光看目录会把删掉的号回收（判据 ⑥）。三个站都要算它，
    规则放一处 —— 三处各写一遍的话，哪天改了记录的形状就会有一站悄悄不算。
    """
    return [parse_serial(Path(one.file)) or 0 for one in entries]


def points_at(cfg: Config, recorded: str, image: Path) -> bool:
    """账里记的那条是不是就是这张图。

    记录里的 file 通常是相对仓库根的（`output/2026-10-24/fish-001.orig.png`），
    也可能折成 `~/...`（仓库外但在家目录里），或者是画成绝对路径的旧条目 ——
    三种都得认，不然人一换工作目录就认不出这是同一张。
    """
    path = Path(recorded)
    if recorded.startswith("~"):
        path = path.expanduser()
    elif not path.is_absolute():
        path = Path(cfg.repo_root) / path
    try:
        return path.resolve() == Path(image).resolve()
    except OSError:
        return False


def find_base(entries: list[Entry], ident: str) -> Entry | None:
    """同号那一条"生成来路"，也就是那张原图。

    **只认 orig**。贴纸和带字成图的 prompt、种子、步数全是从原图来的，链子上的
    中间站（贴纸自己）没有新东西可借；账里同号只剩贴纸时（原图被清走）宁可不借，
    也不去借贴纸那条 —— 那会把 `workflows/fish-sticker.json` 和去背模型的文件名
    当成这张图的配方记下来，写进配方库就是一条假的来路。
    """
    for one in entries:
        if one.id == ident and one.kind == IMAGE_KIND:
            return one
    return None


def entry_for_gen(
    cfg: Config,
    *,
    ident: str,
    path: Path,
    kind: str,
    spec: GenSpec,
    workflow: str,
    model: str,
    version: str,
    source: EntrySource | None = None,
) -> Entry:
    """出片这一站的记录：参数全在 `spec` 里，直接抄下来就是可复现的那一串。"""
    width, height = image_size(path)
    return Entry(
        id=ident,
        created_at=now_iso(),
        kind=kind,
        file=repo_relative(cfg, path),
        width=width,
        height=height,
        workflow=workflow,
        model=model,
        prompt=spec.prompt,
        negative=spec.negative,
        seed=spec.seed,
        steps=spec.steps,
        cfg=spec.cfg,
        comfyui_version=version,
        requested_width=spec.width,
        requested_height=spec.height,
        source=source,
    )


def _from_base(base: Entry | None, field: str) -> str:
    """上一步那条的某一栏；没有上一步就是空串 —— 空着比编一个更像话。"""
    return str(getattr(base, field)) if base is not None else ""


def _borrow_version(version: str | None, base: Entry | None) -> str:
    """这次没问到版本，就借上一条的；上一条也没有，才写「未知」。

    加字那一站根本没碰引擎，去背那一站问不到版本也不该让记录退化成"未知" ——
    同一条链上的图始终是同一个引擎版本出来的。
    """
    if version and version != UNKNOWN_VERSION:
        return version
    if base is not None and base.comfyui_version:
        return base.comfyui_version
    return UNKNOWN_VERSION


def entry_from_base(
    cfg: Config,
    base: Entry | None,
    *,
    ident: str,
    path: Path,
    kind: str,
    workflow: str | None = None,
    model: str | None = None,
    version: str | None = None,
    source: EntrySource | None = None,
    caption: str | None = None,  # glossary-ok: 字段名照 docs/design.md §6.2
    size: tuple[int, int] | None = None,
) -> Entry:
    """去背 / 加字这两站的记录：生成参数从上一步那条借，这里只换掉自己这一截。

    `base` 为空时（对着素材图去背，当天账里没有它的来路）就把生成那几栏留空 ——
    比编一个种子出来诚实：这条记录能回答"这张图是怎么来的"里的后半截，
    前半截本来就不存在。

    `size` 给「画布多大」，省掉一次读图：加字那一站不换画布尺寸，手里的尺寸就是
    成品的；而且那一站的画字后端是可注入的，测试里根本不落盘，去读文件只会读了个空。

    `workflow`/`model` 不填就跟着上一步走 —— 去背站要填它自己的（换模板、换模型），
    加字站两样都不变，让"借上一步"这条规则只在这一处，省得每个调用方各写一遍。
    """
    width, height = size if size is not None else image_size(path)
    return Entry(
        id=ident,
        created_at=now_iso(),
        kind=kind,
        file=repo_relative(cfg, path),
        width=width,
        height=height,
        workflow=workflow if workflow is not None else _from_base(base, "workflow"),
        model=model if model is not None else _from_base(base, "model"),
        prompt=base.prompt if base is not None else "",
        negative=base.negative if base is not None else "",
        seed=base.seed if base is not None else 0,
        steps=base.steps if base is not None else 0,
        cfg=base.cfg if base is not None else 0.0,
        comfyui_version=_borrow_version(version, base),
        # 请求尺寸沿用上一站：去背不换尺寸，成品和请求值的那个差是引擎造成的，得留着。
        requested_width=base.requested_width if base is not None else 0,
        requested_height=base.requested_height if base is not None else 0,
        source=source if source is not None else (base.source if base is not None else None),
        caption=caption,  # glossary-ok: 字段名照 docs/design.md §6.2
    )
