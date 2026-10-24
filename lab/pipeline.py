"""出片管线：一句话进去，一批图出来。

这是第一个把各层串起来的模块，所以它只管**编排**，不管细节：
读模板 → 注参数 → 交引擎 → 等 → 搬运 → 命名 → 逐张报进度与种子。
参数怎么注在 `workflows`，怎么说话在 `comfy`，这里不重复它们的知识。

两条刻意的规矩：
1. **搬运，不引用**。引擎的 output 目录迟早会被清掉，所以图先落到临时目录，
   再 `shutil.move` 到本仓库 `output/` 下 —— 引用等于把成品寄存在别人的回收站里。
2. **每张各自记种子**。种子不落在图上，只能记在记录里；丢了就再也重跑不出同一张。
"""

from __future__ import annotations

import hashlib
import random
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from .comfy import EngineClient, EngineLike, GeneratedImage
from .config import Config
from .naming import daily_dir, entry_id, next_serial, output_name
from .paths import check_image_file
from .recording import (
    IMAGE_KIND,
    engine_version,
    entry_for_gen,
    repo_relative,
    used_serials,
)
from .registry import EntrySource, load_entries, record_path, write_entry
from .workflows import GenSpec, apply, load, model_name

__all__ = [
    "template_slot",
    "IMAGE_KIND",
    "MAX_SEED",
    "MadeImage",
    "MadeImageSource",
    "Progress",
    "SilentProgress",
    "make",
    "move_out",
    "one_image",
    "random_seed",
    "upload_name",
]

#: ComfyUI 的 noise_seed 是 64 位，但超过 2^32 的值在部分节点上会被截；
#: 卡在 32 位整数范围内最省心，也够随机。
MAX_SEED = 2**32 - 1


@runtime_checkable
class Progress(Protocol):
    """进度出口。管线只负责说"到哪一步了"，怎么显示是 CLI 的事。"""

    def start(self, index: int, total: int, seed: int) -> None: ...

    def waiting(self, index: int, total: int, elapsed: float) -> None: ...

    def done(self, index: int, total: int, path: Path, seed: int) -> None: ...


class SilentProgress:
    """什么都不说的进度 —— 给测试和脚本用。"""

    def start(self, index: int, total: int, seed: int) -> None:
        pass

    def waiting(self, index: int, total: int, elapsed: float) -> None:
        pass

    def done(self, index: int, total: int, path: Path, seed: int) -> None:
        pass


@dataclass(frozen=True)
class MadeImageSource:
    """底图复用的来路：用哪张底图、引擎侧叫它什么、重绘强度多大。

    记这个是为了"形象跑掉了"那天能退回去重跑 —— 重绘强度是那张图上唯一的旋钮，
    丢了它就只能靠记忆猜。
    """

    type: str
    image: Path
    denoise: float
    engine_filename: str


@dataclass(frozen=True)
class MadeImage:
    """一张出完的图，连同重跑它需要的全部参数。"""

    path: Path
    serial: int
    spec: GenSpec
    prompt_id: str
    engine_filename: str
    #: 文生图是 None；底图复用记着底图与重绘强度。
    source: MadeImageSource | None = None

    @property
    def id(self) -> str:
        return entry_id(self.serial)


def random_seed() -> int:
    """留空时的随机种子。用 random 而不是 secrets —— 这不是密码。"""
    return random.randint(0, MAX_SEED)


def _specs(base: GenSpec, count: int, seed: int | None) -> list[GenSpec]:
    """给每一张配一个种子。

    指定种子时按 `seed, seed+1, …` 递增 —— 这样"同种子同参数"能重来整批，
    而不是四张全撞成同一张图。
    """
    first = base.seed if seed is None else int(seed)
    return [
        replace(base, seed=(first + offset) % (MAX_SEED + 1)) for offset in range(count)
    ]


def one_image(images: tuple[GeneratedImage, ...], prompt_id: str) -> GeneratedImage:
    """一次提交只该出一张图。

    模板里的 `batch_size` 若被改成 N，一次提交会回来 N 张 —— 它们共用同一个种子，
    没法单独重跑，也就没法满足"每张各自记种子"。宁可当场报错，也不静默扔掉 N-1 张：
    白算几分钟再悄悄丢图，比不开始更糟。
    """
    if len(images) > 1:
        raise ValueError(
            f"模板一次提交出成了 {len(images)} 张（batch_size > 1），"
            "它们共用同一个种子，无法单独重跑。"
            "把工作流里的 batch_size 改回 1，用 --count 控制张数。"
            f"（任务 {prompt_id}）"
        )
    return images[0]


def move_out(engine_file: Path, target: Path) -> Path:
    """把临时目录里的图搬到最终位置。跨文件系统也能搬（shutil.move 会退化成拷贝）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(engine_file), str(target))
    return target


def upload_name(path: Path) -> str:
    """一张图在引擎侧的名字：内容哈希 + 原后缀。

    素材路径里有中文和空格，直接拿原名去引擎容易出岔子；用内容哈希还能让
    "同一张图重复上传"落成同一个名字 —— 上传因此是幂等的，不在引擎里堆垃圾。

    底图复用与去背共用这一套命名：两边都只是"喂一张本机图给引擎"。
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return f"lab-init-{digest}{path.suffix.lower()}"


def template_slot(spec: GenSpec) -> str:
    """这次生成走哪一槽模板：`img2img` 还是 `default`。

    这个判断只有一份：pipeline 拿它挑配置里的文件名，`reproduce` 拿它把配方
    记的那份钉回同一槽（配方是权威，但"哪一槽"仍由这里说了算 —— 两处各判一次，
    迟早会有一处把有底图的那张当成文生图）。
    """
    return "default" if spec.init_image is None else "img2img"


def _template_name(cfg: Config, spec: GenSpec) -> str:
    """有底图走底图模板，没有就走文生图 —— 这是两张不同的图，不是同一个旋钮。"""
    slot = template_slot(spec)
    name = cfg.workflows.img2img if slot == "img2img" else cfg.workflows.default
    if not name:
        raise ValueError(
            "给了底图，但 [workflows].img2img 没配模板名 —— "
            "去 config/default.toml 里补上 img2img 模板的文件名。"
        )
    return name


def make(
    cfg: Config,
    spec: GenSpec,
    count: int | None = None,
    seed: int | None = None,
    progress: Progress | None = None,
    client: EngineLike | None = None,
    today: date | None = None,
    reserved: Iterable[int] = (),
) -> list[MadeImage]:
    """出一批图。返回每一张的落盘路径与它自己的种子。

    带底图时（spec.init_image）走另一条路：底图先上传一次进引擎的 input 目录，
    再按 denoise 重绘 —— 这是第一阶段把形象钉住的主力手段。

    失败会往外抛 `comfy.EngineError` 家族 —— 管线不把引擎的故障翻译成"出错了"，
    那层翻译是 comfy 的活，这里只负责让已经出成的图不丢。

    `reserved` 是"这几个号别用"。平常不需要 —— 账与目录一起算着号，不会回收。
    一个它会用到的场景：复现时原图已经被清走、当天的账也一起没了（`output/` 整目录
    被清空间清掉），号就会从配方那条重新数起，新图正好落到原图那个位置，把配方
    自己那条记录的落点顶掉。调用方知道这种情况，把那个号递进来。
    """
    total = cfg.generate.count if count is None else int(count)
    if total < 1:
        raise ValueError("--count 至少是 1")

    init_image = (
        check_image_file(spec.init_image, what="底图")
        if spec.init_image is not None
        else None
    )
    if init_image is not None and not 0.0 < float(spec.denoise) <= 1.0:
        raise ValueError(
            f"重绘强度得在 0 和 1 之间（不含 0）：给了 {spec.denoise} —— "
            "太大丢形象，太小改不动表情，0.45 是不错的起点。"
        )

    report = progress or SilentProgress()
    engine = client or EngineClient(host=cfg.comfy.host, port=cfg.comfy.port)
    template = load(Path(cfg.repo_root) / "workflows" / _template_name(cfg, spec))

    # 底图先落到引擎的 input 目录：LoadImage 只认它自己目录里的名字。
    # 整批只传一次 —— 同一张底图的每一张成品共用它。
    engine_name = (
        engine.upload_image(init_image, name=upload_name(init_image))
        if init_image is not None
        else None
    )

    out_dir = daily_dir(cfg.paths.output_dir, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = record_path(cfg.paths.output_dir, today)
    # 号只增不收：目录里的号 + 当天记录里用过的号，取两者最大值再往前一格。
    # 只看目录的话，废图清走之后号会被回收，两条不同的生成顶着同一个 id。
    known = [*used_serials(load_entries(ledger)), *reserved]
    serial = next_serial(out_dir, known=known)
    template_name = _template_name(cfg, spec)
    version = engine_version(engine)
    model = model_name(template)

    targets = _specs(spec, total, seed)
    made: list[MadeImage] = []

    for offset, one in enumerate(targets):
        index = offset + 1
        report.start(index, total, one.seed)

        # 临时目录只活这一次：引擎侧的文件名（fish/txt2img_00001_.png）不能直接当
        # 成品名用，搬到这里再改名，顺手把"搬运"这件事变成不可省的一步。
        with tempfile.TemporaryDirectory(prefix="lab-engine-") as staging:
            prompt = apply(template, one, engine_name=engine_name)
            prompt_id = engine.submit(prompt)
            result = engine.wait(prompt_id, on_poll=_ticker(report, index, total))
            image = one_image(result.images, prompt_id)
            fetched = engine.fetch(image, Path(staging))

            name = output_name(serial + offset, IMAGE_KIND)
            final = move_out(fetched, out_dir / name)

        if init_image is None:
            source = None
        else:
            source = MadeImageSource(
                type="img2img",
                image=init_image,
                denoise=one.denoise,
                engine_filename=str(engine_name),
            )

        record = MadeImage(
            path=final,
            serial=serial + offset,
            spec=one,
            prompt_id=prompt_id,
            engine_filename=image.filename,
            source=source,
        )
        source_entry = None
        if source is not None:
            source_entry = EntrySource(
                type=source.type,
                image=repo_relative(cfg, source.image),
                denoise=source.denoise,
            )
        # 一张一落账，不攒到整批结束：这张已经成了，就不该被下一张的失败带走。
        write_entry(
            ledger,
            entry_for_gen(
                cfg,
                ident=record.id,
                path=final,
                kind=IMAGE_KIND,
                spec=one,
                workflow=f"workflows/{template_name}",
                model=model,
                version=version,
                source=source_entry,
            ),
        )
        made.append(record)
        report.done(index, total, final, one.seed)

    return made


def _ticker(report: Progress, index: int, total: int):
    """把引擎的每次轮询翻译成一次"还在等" —— 这就是轮询能给的最大信息量。

    真正的"当前第几步"只有 WebSocket 的 progress 消息才有；轮询拿不到，
    所以这里报的是已等待时长，不假装知道步数。
    """

    def tick(polls: int, elapsed: float) -> None:
        _ = polls
        report.waiting(index, total, elapsed)

    return tick
