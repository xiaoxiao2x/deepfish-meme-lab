"""后处理：把出好的图变成能直接用的贴纸。

模型推理在引擎里（docs/design.md:105）：去背是 ComfyUI-RMBG 的节点干的活，
这里负责编排这一趟 —— 把哪张图喂进去、出来的东西验一验、叫什么、落哪儿。

命名沿用 `fish-###.{orig|sticker|captioned}.png`（docs/roadmap.md:155）：贴纸与它的
原图共用一个序号 —— `fish-007.orig.png` 去背出来就是 `fish-007.sticker.png`，
两张图躺在同一个目录里，对着看很方便。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .comfy import EngineClient, EngineError
from .config import Config
from .paths import check_image_file
from .pipeline import (
    daily_dir,
    move_out,
    next_serial,
    one_image,
    parse_serial,
    upload_name,
)
from .workflows import apply_sticker, load, sticker_model

__all__ = ["STICKER_KIND", "MadeSticker", "sticker"]

#: 贴纸的后缀：`fish-007.sticker.png` 里那一段（docs/roadmap.md:155）。
STICKER_KIND = "sticker"


@dataclass(frozen=True)
class MadeSticker:
    """一张去好背的贴纸。

    from_image 记的是喂进去那张图的绝对路径 —— "这张贴纸是从哪张图来的"只有它能
    回答，想重做一张的时候也是照它重来。叫 from_image 而不是 source：design.md
    §6.2 里的 source 说的是原图那条生成来路（底图/重绘强度），两回事。
    """

    path: Path
    serial: int
    from_image: Path
    model: str
    prompt_id: str
    engine_filename: str

    @property
    def id(self) -> str:
        return f"fish-{self.serial:03d}"


def _plan_output(cfg: Config, source: Path, today: date | None) -> tuple[Path, int]:
    """贴纸落哪儿、叫什么号。

    源图就在 output/ 里时贴着它落 —— 贴纸和原图放一起才方便对着看；素材图
    （在素材根里）不往那儿写，落到今天那个目录，按当天最大号接着排。

    序号沿用源图的：fish-007 的原图与贴纸是同一个配方条目的两个面。
    同名贴纸已经存在就直接覆盖 —— 那就是"把这张重做一遍"。
    """
    output_dir = Path(cfg.paths.output_dir)
    folder = (
        source.parent if output_dir in source.parents else daily_dir(output_dir, today)
    )
    serial = parse_serial(source)
    if serial is None:
        serial = next_serial(folder)
    return folder / f"fish-{serial:03d}.{STICKER_KIND}.png", serial


def _check_alpha(path: Path) -> None:
    """成品必须真的是带 alpha 的 RGBA，否则"透明底贴纸"这句话就是假的。

    去背节点有三路输出（IMAGE / MASK / MASK_IMAGE）：接 MASK 那一路引擎当场就拒
    （SaveImage 要 IMAGE，类型不匹配，实测 0.42.0 返回 400）；MASK_IMAGE 那一路类型
    一样、能跑完，只是透明区在保存时没了 —— 所以这里回读一遍再确认。

    抛 EngineError 而不是模板错：模板没毛病，是这一趟回来的图不对。
    """
    from PIL import Image

    with Image.open(path) as image:
        if image.mode != "RGBA":
            raise EngineError(
                f"去背回来的图是 {image.mode}，没有 alpha 通道：{path.name} —— "
                "「去背」节点的输出要接 background=Alpha 那一路（IMAGE）再接 "
                "SaveImage；接 MASK_IMAGE 也能跑完，但透明区会丢。"
            )


def sticker(
    cfg: Config,
    image: Path | str,
    client: EngineClient | None = None,
    today: date | None = None,
) -> MadeSticker:
    """把一张图去背成透明底 PNG（票 #9）。

    骨架和 lab make 一样：上传 → 提交 → 搬回来。差别只有两处 —— 这里一次只出
    一张，且成品名沿用源图那个序号。

    对着非工作台生成的图同样有效：任一张本机图片都能喂进来，跟"哪来的"无关。

    同名贴纸已经存在就原地覆盖 —— 那是"把这张重来一遍"，不是多出一张。
    """
    source = check_image_file(image, what="图片")

    if source.suffix.lower() == ".gif":
        raise ValueError(
            f"动图去背这一阶段不做：{source} —— 引擎只会取第一帧，出来的东西跟"
            "你要的不是一回事（docs/roadmap.md:170）。先存成静帧再喂进来。"
        )

    template_name = cfg.workflows.sticker
    if not template_name:
        raise ValueError(
            "没配去背模板：[workflows].sticker 是空的 —— "
            "去 config/default.toml 里补上模板文件名。"
        )
    workflow = load(Path(cfg.repo_root) / "workflows" / template_name)

    engine = client or EngineClient(host=cfg.comfy.host, port=cfg.comfy.port)
    engine_name = engine.upload_image(source, name=upload_name(source))
    prompt = apply_sticker(workflow, engine_name)

    prompt_id = engine.submit(prompt)
    result = engine.wait(prompt_id)
    engine_image = one_image(result.images, prompt_id)

    target, serial = _plan_output(cfg, source, today)
    # 先验后搬：一张假贴纸（丢了 alpha）不该落进 output/ 冒充成品。
    with tempfile.TemporaryDirectory(prefix="lab-engine-") as staging:
        fetched = engine.fetch(engine_image, Path(staging))
        _check_alpha(fetched)
        final = move_out(fetched, target)

    return MadeSticker(
        path=final,
        serial=serial,
        from_image=source,
        # 回执里那只模型是从交出去的 prompt 里读的，也就是模板里写的那只。
        model=sticker_model(prompt),
        prompt_id=prompt_id,
        engine_filename=engine_image.filename,
    )
