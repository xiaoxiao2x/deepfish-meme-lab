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

import random
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable

from .comfy import EngineClient, GeneratedImage
from .config import Config
from .workflows import GenSpec, apply, load

__all__ = [
    "IMAGE_KIND",
    "MAX_SEED",
    "MadeImage",
    "Progress",
    "SilentProgress",
    "daily_dir",
    "make",
    "next_serial",
    "random_seed",
]

#: ComfyUI 的 noise_seed 是 64 位，但超过 2^32 的值在部分节点上会被截；
#: 卡在 32 位整数范围内最省心，也够随机。
MAX_SEED = 2**32 - 1

#: 这一阶段只出原图。去背（sticker）与台词（captioned）是 #9 / #10 的事。
IMAGE_KIND = "orig"


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
class MadeImage:
    """一张出完的图，连同重跑它需要的全部参数。"""

    path: Path
    serial: int
    spec: GenSpec
    prompt_id: str
    engine_filename: str

    @property
    def id(self) -> str:
        return f"fish-{self.serial:03d}"


def random_seed() -> int:
    """留空时的随机种子。用 random 而不是 secrets —— 这不是密码。"""
    return random.randint(0, MAX_SEED)


def daily_dir(output_dir: Path, today: date | None = None) -> Path:
    """`output/YYYY-MM-DD`。按本地日期分堆，方便当天挑图。"""
    day = today or date.today()
    return Path(output_dir) / day.isoformat()


def next_serial(directory: Path, kind: str = IMAGE_KIND) -> int:
    """当天目录里下一个可用序号。

    序号是全天的（不是按 kind 分），因为 `fish-001` 是同一个配方条目的 id：
    同一张图的原图、贴纸、带字成图共享一个序号，靠 kind 后缀区分。
    已经存在的最大序号 +1，既不会撞车，也不会把删掉的号回收再利用。
    """
    directory = Path(directory)
    if not directory.is_dir():
        return 1

    highest = 0
    # 不按 kind 过滤：fish-001.orig.png 与 fish-001.sticker.png 共享一个序号，
    # 只看同 kind 会让后续阶段撞 id。
    for path in directory.glob("fish-*.png"):
        stem = path.name.split(".", 1)[0]  # fish-001
        _, _, digits = stem.partition("-")
        if digits.isdigit():
            highest = max(highest, int(digits))

    return highest + 1


def _specs(base: GenSpec, count: int, seed: int | None) -> list[GenSpec]:
    """给每一张配一个种子。

    指定种子时按 `seed, seed+1, …` 递增 —— 这样"同种子同参数"能重来整批，
    而不是四张全撞成同一张图。
    """
    first = base.seed if seed is None else int(seed)
    return [
        replace(base, seed=(first + offset) % (MAX_SEED + 1)) for offset in range(count)
    ]


def _one_image(images: tuple[GeneratedImage, ...], prompt_id: str) -> GeneratedImage:
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


def _move_out(engine_file: Path, target: Path) -> Path:
    """把临时目录里的图搬到最终位置。跨文件系统也能搬（shutil.move 会退化成拷贝）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(engine_file), str(target))
    return target


def make(
    cfg: Config,
    spec: GenSpec,
    count: int | None = None,
    seed: int | None = None,
    progress: Progress | None = None,
    client: EngineClient | None = None,
    today: date | None = None,
) -> list[MadeImage]:
    """出一批图。返回每一张的落盘路径与它自己的种子。

    失败会往外抛 `comfy.EngineError` 家族 —— 管线不把引擎的故障翻译成"出错了"，
    那层翻译是 comfy 的活，这里只负责让已经出成的图不丢。
    """
    total = cfg.generate.count if count is None else int(count)
    if total < 1:
        raise ValueError("--count 至少是 1")

    report = progress or SilentProgress()
    engine = client or EngineClient(host=cfg.comfy.host, port=cfg.comfy.port)
    template = load(Path(cfg.repo_root) / "workflows" / cfg.workflows.default)

    out_dir = daily_dir(cfg.paths.output_dir, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    serial = next_serial(out_dir)

    targets = _specs(spec, total, seed)
    made: list[MadeImage] = []

    for offset, one in enumerate(targets):
        index = offset + 1
        report.start(index, total, one.seed)

        # 临时目录只活这一次：引擎侧的文件名（fish/txt2img_00001_.png）不能直接当
        # 成品名用，搬到这里再改名，顺手把"搬运"这件事变成不可省的一步。
        with tempfile.TemporaryDirectory(prefix="lab-engine-") as staging:
            prompt = apply(template, one)
            prompt_id = engine.submit(prompt)
            result = engine.wait(prompt_id, on_poll=_ticker(report, index, total))
            image = _one_image(result.images, prompt_id)
            fetched = engine.fetch(image, Path(staging))

            name = f"fish-{serial + offset:03d}.{IMAGE_KIND}.png"
            final = _move_out(fetched, out_dir / name)

        record = MadeImage(
            path=final,
            serial=serial + offset,
            spec=one,
            prompt_id=prompt_id,
            engine_filename=image.filename,
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
