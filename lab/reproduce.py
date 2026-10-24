"""复现：拿配方库里的那条重跑，看它还能不能出回原来那张（票 #12）。

**这一站为什么存在**：配方库记下的 prompt / 种子 / 模型 / 引擎版本，唯一的用处
就是"还能再出一张一样的"。没有一条命令去真的跑一遍，那份记录只是好看的字。

**它不新写一套出片流程**：重跑就是 pipeline.make 出一张 —— 落盘、编号、
写当天账都是那一层已经在做的事。这一站只负责把"一条配方"翻译成一次生成，
再把结果与原图比一遍。

**它只读配方库，不写**：重跑出来的那张进当天的全量记录（判据 ④），但不自动
进配方库 —— 定稿是个动作（lab keep），重跑一次不等于把它扶正。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from .comfy import EngineClient, EngineLike
from .config import Config
from .keeping import IMPORTED_KIND
from .naming import parse_serial
from .pipeline import MadeImage, Progress, make, template_slot
from .recording import UNKNOWN_VERSION, engine_version, find_base, resolve_recorded
from .registry import Entry, library_path, load_entries
from .workflows import GenSpec, load, missing_titles, model_name

__all__ = ["Reproduction", "reproduce"]


#: 重跑一条配方最少要这几栏有值。空串不是"没填"的委婉说法，是这条记录
#: 重不出来 —— keep 收下的外来图就是这样：图的事实齐全，参数那一栏空着。
_RUNNABLE = ("workflow", "model", "prompt", "steps", "cfg")


@dataclass(frozen=True)
class Reproduction:
    """一次重跑的结果：配方、新图、像不像、有没有额外的话要说。"""

    #: 配方库里的那一条。
    recipe: Entry

    #: 这次重跑出来的那张（新号，落在今天那个目录里）。
    image: MadeImage

    #: 与原图逐像素一致吗。None 表示原图不在，无从比起 —— 不是"一致"。
    same: bool | None

    #: 给人看的一句话：版本对不上、原图不在之类，都写在这儿。
    note: str = ""


def _recipe(cfg: Config, ident: str) -> Entry:
    """从配方库里取出这个号的原图那一条。"""
    entries = load_entries(library_path(cfg.repo_root))
    found = find_base(entries, ident)
    if found is not None:
        return found
    # 同号但只有别的阶段：说清楚"不是没有，是不能拿它重跑"，别让人以为号丢了。
    other = next((one for one in entries if one.id == ident), None)
    if other is None:
        raise ValueError(
            f"配方库里没有 {ident} —— 先用 python -m lab gallery 看有哪些号，"
            "要重跑的那张得先被 keep 进配方库。"
        )
    if other.kind == IMPORTED_KIND:
        # 外来的那张：记的是图本身，不是出它的那次生成 —— 这一条注定重不出来。
        raise ValueError(
            f"{ident} 是 keep 收下的外来图（imported），当天账里没有它的来路，"
            "生成那几栏是空的 —— 它没有配方可重跑，只能留在库里当图看。"
        )
    raise ValueError(
        f"配方库里 {ident} 只有 {other.kind} 那一条，它是从原图派生的。"
        f"重跑要用同号的原图（{ident} 的 orig）—— "
        "去 gallery 看看这个号还在不在。"
    )


def _check_runnable(entry: Entry) -> None:
    """能重跑吗。缺哪一栏就说到哪一栏，别让人对着"复现失败"猜。"""
    # 0 也当"不知道"：Entry 里没填的数值一律是 0（种子、步数、引导强度），
    # 而 0 步、0 引导强度喂给引擎出的不是同一张，是随便哪一张。
    blank = [name for name in _RUNNABLE if not getattr(entry, name)]
    if blank:
        raise ValueError(
            f"{entry.id} 这条记不下重跑要的东西：{'、'.join(blank)} 是空的。"
            "这种条目是 keep 时当天账里没有来路的那张（外来的图，或者账被清走了），"
            "它记的是这张图本身，不是出它的那次生成。"
        )


def _check_template(cfg: Config, entry: Entry) -> None:
    """配方指的那份模板，顺带核对它还是原来那份。

    模板是仓库里的文件，会被改。改了之后同一条配方跑出来的就不是原来那张 ——
    与其让人对着两张不一样的图猜，不如在这儿说清楚是哪一栏对不上。
    """
    path = Path(cfg.repo_root) / entry.workflow
    if not path.is_file():
        raise ValueError(
            f"{entry.id} 的配方指着 {entry.workflow}，这个文件不在"
            f"（{path}）—— 模板改名或删了，这条配方重不出来。"
        )
    template = load(path)
    missing = missing_titles(template)
    if missing:
        raise ValueError(
            f"{entry.workflow} 里缺了 {'、'.join(missing)} 这些节点 —— "
            "模板改过了，这条配方引用的接线已经不是当初那一套。"
        )
    actual = model_name(template)
    if actual != entry.model:
        raise ValueError(
            f"{entry.workflow} 现在写的是模型 {actual}，"
            f"{entry.id} 的配方记的是 {entry.model} —— 换过模型，同种子也出不来同一张。"
        )


def _spec(cfg: Config, entry: Entry) -> GenSpec:
    """一条配方 → 一次生成要的那串参数。"""
    spec = GenSpec(
        prompt=entry.prompt,
        negative=entry.negative,
        seed=entry.seed,
        steps=entry.steps,
        cfg=entry.cfg,
        # 尺寸喂的是当初请求的那个：成品量出来的宽高是引擎缩过之后的结果，
        # 照它喂会越跑越小（docs/design.md §6.2）。
        width=entry.requested_width or entry.width,
        height=entry.requested_height or entry.height,
    )
    if entry.source is None:
        return spec
    # 底图复用出来的那张：重跑也得拿同一张底图，否则换了输入就换了图。
    init_image = resolve_recorded(cfg, entry.source.image)
    if not init_image.is_file():
        raise ValueError(
            f"{entry.id} 的配方拿 {entry.source.image} 当底图，这张不在了 —— "
            "底图是配方的输入，丢了它重不出来。"
        )
    return replace(spec, init_image=init_image, denoise=entry.source.denoise)


def _pinned(cfg: Config, entry: Entry, spec: GenSpec) -> Config:
    """把模板钉死在配方指的那一份上。

    不这么做的话，重跑用的是 config 此刻的默认模板 —— 谁改过一次
    `[workflows].default`，同一条配方就悄悄换了一套接线，而记录上还写着原来那份。
    配方自带模板名，它就是权威。
    """
    name = Path(entry.workflow).name
    # 走哪一槽的问 pipeline（template_slot）—— 那里已经判过一次，这儿再判一次
    # 迟早会有一处把有底图的那张当成文生图。
    if template_slot(spec) == "img2img":
        return replace(cfg, workflows=replace(cfg.workflows, img2img=name))
    return replace(cfg, workflows=replace(cfg.workflows, default=name))


@dataclass(frozen=True)
class _Look:
    """一张画的样子：尺寸 + RGB 像素。比图只比这两样。

    不比文件字节：PNG 的压缩参数变了不代表图画变了，反过来"字节不一样"
    也不该被说成"图不一样"。
    """

    size: tuple[int, int]
    rgb: bytes

    @classmethod
    def of(cls, path: Path) -> _Look:
        from PIL import Image

        with Image.open(path) as opened:
            return cls(size=opened.size, rgb=opened.convert("RGB").tobytes())


def _original(cfg: Config, entry: Entry) -> _Look | None:
    """原图现在的样子，量不出来就是 None。**要在重跑之前量**。

    先量后跑不是洁癖：重跑落在同一个日期目录里，号又是只增不收的，
    原图被清走之后那个号会被这次重跑捡回去 —— 跑完再去看"原图那个路径"，
    看到的是刚出来的新图，拿它跟自己比当然一致。这个坑真踩到过。
    """
    original = resolve_recorded(cfg, entry.file)
    if not original.is_file():
        return None
    return _Look.of(original)


def _compare(entry: Entry, before: _Look | None, made: MadeImage) -> tuple[bool | None, str]:
    """新图与原图比一遍，返回（像不像，要补充的一句话）。"""
    if before is None:
        return None, f"原图 {entry.file} 不在了，这次没得比 —— 只验了它还能跑出片。"
    return _Look.of(made.path) == before, ""


def _reserved(cfg: Config, entry: Entry) -> tuple[int, ...]:
    """原图那个号别让这次重跑捡回去。

    号是从"当天的账 + 落点目录"里算的；两者都被清走（整个 output/ 删掉清空间）
    之后会从 1 重新数，新图正好落在原图那个位置 —— 那份配方自己指着的落点就
    变成了一张别的图，再重跑一次就成了自己跟自己比。所以原图那个号一律留出来。
    """
    serial = parse_serial(Path(entry.file))
    return () if serial is None else (serial,)


def _version_note(entry: Entry, engine: object, same: bool | None) -> str:
    """引擎版本对不上时说一句 —— 这是"两张不一样"最常见的解释。"""
    actual = engine_version(engine)
    if actual == entry.comfyui_version:
        return ""
    if UNKNOWN_VERSION in (actual, entry.comfyui_version):
        # 「不知道」不是「不一样」：说成"版本不一样"会把人引到错的地方去查。
        return (
            f"引擎版本对不上号：配方记的是 {entry.comfyui_version}，这次是 {actual} —— "
            "两边是不是同一个版本说不准，也就解释不了这两张为什么一样或者不一样。"
        )
    if same is True:
        # 一致了也要说：判据 ② 认的是"同引擎版本下一致"，跨版本一致算运气。
        return (
            f"引擎版本不一样：配方记的是 {entry.comfyui_version}，这次是 {actual}。"
            "这次照样一致，但同种子跨版本不保证一致（docs/roadmap.md 的风险那一条）。"
        )
    return (
        f"引擎版本不一样：配方记的是 {entry.comfyui_version}，这次是 {actual}。"
        "同种子跨版本不保证一致（docs/roadmap.md 的风险那一条）—— "
        "先换回那个版本再比一次。"
    )


def reproduce(
    cfg: Config,
    ident: str,
    *,
    progress: Progress | None = None,
    client: EngineLike | None = None,
    today: date | None = None,
) -> Reproduction:
    """重跑 ident 那条配方，产出一张新图并落进当天的全量记录。

    失败往外抛 ValueError（配方本身的问题）或 comfy.EngineError 家族
    （引擎的问题）—— 和另外几站一样，命令行那一层负责把它们说成人话。
    """
    entry = _recipe(cfg, ident)
    _check_runnable(entry)
    _check_template(cfg, entry)
    spec = _spec(cfg, entry)

    # 原图先量下来：跑完之后那个路径可能已经被这次的新图占回去了（见 _original）。
    before = _original(cfg, entry)

    # 引擎在这儿建，不留给 make 自己建：跑完要问一句版本，问不到就没法解释
    # "两张不一样"是不是换过引擎（判据 ② 认的是同引擎版本下的一致）。
    engine = client or EngineClient(host=cfg.comfy.host, port=cfg.comfy.port)
    pinned = _pinned(cfg, entry, spec)
    made = make(
        pinned,
        spec,
        count=1,
        progress=progress,
        client=engine,
        today=today,
        reserved=_reserved(cfg, entry),
    )
    (one,) = made

    same, note = _compare(entry, before, one)
    version_note = _version_note(entry, engine, same)
    return Reproduction(
        recipe=entry,
        image=one,
        same=same,
        note=" ".join(part for part in (note, version_note) if part),
    )
