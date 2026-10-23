"""命令行入口。

    python -m lab doctor          检查环境齐不齐
    python -m lab assets scan     扫描素材，写索引，报张数与重复
    python -m lab assets dupes    按索引列出重复组
    python -m lab make            给一句话，出一批图（这是工作台的主要用途）
    python -m lab sticker <图片>  把一张图去背成透明底贴纸
    python -m lab stub            起一个假引擎（无显卡也能端到端调试）

退出码：0 = 全绿能干活；1 = 有 FAIL、有没查成的项、或有问题要处理；2 = 命令本身
用错了（参数不对、配置读不出来）。WARN 不算通过 —— 「没配所以没查」和「查了没
问题」是两回事，混在一起会在接线之前就把问题咽掉。

**顶层只 import 标准库 + doctor**：素材索引要 Pillow，所以它是在 `_run_assets`
里才 import 的。这样「没装 Pillow 的机器也能跑 `lab doctor`」不是一句口号。
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config, ConfigError, load_config
from .doctor import Status, is_ready, run_doctor

if TYPE_CHECKING:
    from .assets import Index

__all__ = ["main"]

REPO_ROOT = Path(__file__).resolve().parent.parent

BADGE = {Status.OK: "[ OK ]", Status.WARN: "[WARN]", Status.FAIL: "[FAIL]"}
RULE = "─" * 60
LABEL_WIDTH = 14  # 按显示宽度算，所以中英文标签都能对齐
KIND_LABEL = {"exact": "字节一致", "perceptual": "画面近似", "suspect": "疑似同图"}


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度右补空格 —— 一个汉字占两格，不能用 str.ljust。"""
    return text + " " * max(0, width - _display_width(text))


def _section(title: str, lines: list[str], footer: str | None = None) -> None:
    print()
    print(title)
    print(RULE)
    for line in lines:
        print(line)
    if footer is not None:
        print(RULE)
        print(footer)
    print(RULE)
    print()


def _print_report(results) -> None:
    lines = [
        f"{BADGE[r.status]} {_pad(r.label, LABEL_WIDTH)} {r.detail}" for r in results
    ]
    fails = sum(1 for r in results if r.status is Status.FAIL)
    warns = sum(1 for r in results if r.status is Status.WARN)
    if fails:
        footer = f"{fails} 项没通过，{warns} 项待办 —— 先解决上面的 FAIL。"
    elif warns:
        footer = f"{warns} 项还没配、没查 —— 引擎能跑不等于工作台能干活。"
    else:
        footer = "全绿。"
    _section("鱼片加工厂 · 环境体检", lines, footer)


def _print_assets_report(index: Index, index_path: Path) -> None:
    animated = sum(1 for a in index.assets if a.animated)
    exact = sum(1 for g in index.duplicates if g.kind == "exact")
    perceptual = sum(1 for g in index.duplicates if g.kind == "perceptual")
    reachable = sum(1 for root in index.roots if root.exists())
    duplicated = len(index.duplicate_paths)

    rows = [
        ("根目录", f"配置 {len(index.roots)} 个，可达 {reachable} 个"),
        ("张数", f"{index.count} 张（其中动图 {animated}）"),
        ("不重复", f"{len(index.unique)} 张 · 重复涉及 {duplicated} 张"),
        ("重复", f"{exact + perceptual} 组（字节一致 {exact}、画面近似 {perceptual}）"),
    ]
    if index.suspects:
        rows.append(("疑似", f"{len(index.suspects)} 组（哈希像、画面不太像，得人看）"))
    if index.candidates:
        span = (
            f"，MAE {index.vetoed_mae[0]}–{index.vetoed_mae[1]}"
            if index.vetoed_mae
            else ""
        )
        rows.append(
            (
                "收口",
                f"第二级候选 {index.candidates} 对，第三级拦下 {index.vetoed} 对{span}",
            )
        )
    rows.append(("索引", str(index_path)))

    body = [f"{_pad(label, LABEL_WIDTH)} {value}" for label, value in rows]
    body += [f"[注意] {problem}" for problem in index.problems]
    _section("鱼片加工厂 · 素材索引", body)


def _print_dupes(index: Index) -> None:
    lines: list[str] = []
    if not index.duplicates:
        lines.append("没发现重复。")
    for position, group in enumerate(index.duplicates, start=1):
        label = KIND_LABEL.get(group.kind, group.kind)
        extra = f"，MAE {group.mae}" if group.mae is not None else ""
        lines.append(f"[{position}] {label} · {len(group.paths)} 张{extra}")
        lines.extend(f"      {path}" for path in group.paths)
    _section("鱼片加工厂 · 重复素材", lines)


def _run_assets(
    config: Config, action: str | None, verify_readonly: bool = False
) -> int:
    # 到这里才拉 Pillow —— 见文件头的说明
    from .assets import (
        StaleIndex,
        build_index,
        load_index,
        missing_from_disk,
        save_index,
    )

    index_path = config.paths.asset_index

    if action == "dupes":
        if not index_path.exists():
            print(
                f"还没有索引：{index_path}\n先跑 `python -m lab assets scan`。",
                file=sys.stderr,
            )
            return 2
        try:
            index = load_index(index_path)
        except StaleIndex as exc:
            print(f"{exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(
                f"索引读不出来（{exc}）—— 重跑 `python -m lab assets scan` 重建。",
                file=sys.stderr,
            )
            return 2

        missing = missing_from_disk(index)
        if missing:
            print(
                f"索引已过期：{len(missing)} 张素材不在原位了"
                f"（例如 {missing[0]}）。\n"
                "素材挪过位置就重跑 `python -m lab assets scan`。",
                file=sys.stderr,
            )
            return 1
        _print_dupes(index)
        return 0

    if action != "scan":
        print("用法：python -m lab assets scan|dupes", file=sys.stderr)
        return 2

    index = build_index(config.paths.asset_roots, verify_readonly=verify_readonly)
    save_index(index, index_path)
    _print_assets_report(index, index_path)
    return 1 if index.problems else 0


class _MakeProgress:
    """把管线的进度说给终端听。

    两条刻意的克制：
    - **不刷屏**：等不到 3 秒不吭声，之后每 2 秒最多一行。一张图要 2.5 秒，
      每张都打"已等 0.5 秒"只会把有用的路径和种子顶出屏幕。
    - **不假装知道步数**：轮询只能知道"等了多久"。真实步数要订阅引擎的 WebSocket
      progress 消息，那一层留到确实需要时再做（见 docs/design.md）。
    """

    QUIET_UNTIL = 3.0  # 秒
    REFRESH_EVERY = 2.0  # 秒

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdout
        self._last = 0.0

    def start(self, index: int, total: int, seed: int) -> None:
        self._last = 0.0
        print(f"[{index}/{total}] 出图中 · 种子 {seed}", file=self._stream, flush=True)

    def waiting(self, index: int, total: int, elapsed: float) -> None:
        if elapsed < self.QUIET_UNTIL:
            return
        if elapsed - self._last < self.REFRESH_EVERY:
            return
        self._last = elapsed
        print(
            f"[{index}/{total}] 还在算 · 已等 {elapsed:.0f} 秒",
            file=self._stream,
            flush=True,
        )

    def done(self, index: int, total: int, path: Path, seed: int) -> None:
        print(
            f"[{index}/{total}] {path}  seed={seed}",
            file=self._stream,
            flush=True,
        )


def _run_make(config: Config, args: argparse.Namespace) -> int:
    """`lab make` —— 一句话进去，一批图出来。

    参数覆盖顺序：命令行 > config/default.toml 的 [generate]。这一层是**业务参数**，
    翻译成节点参数是 workflows.py 的事，所以这里只拼一个 GenSpec 就交出去。
    """
    from .comfy import EngineError
    from .pipeline import make, random_seed
    from .workflows import GenSpec, WorkflowError

    prompt = (args.prompt or "").strip()
    if not prompt:
        print("--prompt 不能是空的：总得告诉它画什么。", file=sys.stderr)
        return 2

    if args.denoise is not None and not args.init_image:
        print(
            "--denoise 只在给了 --init-image 时才有用：没有底图就没有重绘，"
            "这个旋钮会被忽略。补上 --init-image，或者去掉 --denoise。",
            file=sys.stderr,
        )
        return 2

    gen = config.generate
    prefix = gen.prompt_prefix
    spec = GenSpec(
        prompt=f"{prefix}{prompt}" if prefix else prompt,
        negative=args.negative if args.negative is not None else gen.negative,
        # 种子留空即随机；填了就锁死，批量时从它往上递增（见 pipeline._specs）
        seed=args.seed if args.seed is not None else random_seed(),
        steps=args.steps if args.steps is not None else gen.steps,
        cfg=args.cfg if args.cfg is not None else gen.cfg,
        width=args.width if args.width is not None else gen.width,
        height=args.height if args.height is not None else gen.height,
        # 有底图就走另一条路：同一只形象换表情、换姿势，靠 denoise 把画面钉住
        init_image=Path(args.init_image) if args.init_image else None,
        denoise=args.denoise if args.denoise is not None else gen.denoise,
    )

    # --workflow 是文件名覆盖，得让 Config 那一层也认；用 dataclasses.replace 临时换掉。
    # 只换「这次会用到的那一份」：有底图走 img2img 那份，没有就走 default ——
    # 一把旋钮只该改一件事，另一份模板留着不动。
    if args.workflow:
        from dataclasses import replace as _replace

        workflows = (
            _replace(config.workflows, img2img=args.workflow)
            if args.init_image
            else _replace(config.workflows, default=args.workflow)
        )
        config = _replace(config, workflows=workflows)

    # 起点种子已经写进 spec 了，不再往 make 传 seed —— 让它从 spec.seed 往上递增，
    # 这样「同种子同参数」重来的就是整批，而不是四张撞成一张。
    try:
        made = make(
            config,
            spec,
            count=args.count,
            progress=_MakeProgress(),
        )
    except WorkflowError as exc:
        print(f"工作流模板有问题：{exc}", file=sys.stderr)
        return 1
    except EngineError as exc:
        print(f"引擎没干成：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    if not made:
        print("一张也没出。", file=sys.stderr)
        return 1

    folder = made[0].path.parent
    print()
    if made[0].source is not None:
        print(f"底图 {made[0].source.image} · 重绘强度 {made[0].source.denoise}")
    print(f"出了 {len(made)} 张，在 {folder}")
    for one in made:
        print(f"  {one.id}  seed={one.spec.seed}  {one.path.name}")
    return 0


def _run_sticker(config: Config, args: argparse.Namespace) -> int:
    """`lab sticker` —— 一张图进去，透明底贴纸出来（票 #9）。

    去背的推理在引擎里（ComfyUI-RMBG 的节点），这里只管把图递过去、把成品收好。
    """
    from .comfy import EngineError
    from .postprocess import sticker
    from .workflows import WorkflowError

    try:
        made = sticker(config, args.image)
    except WorkflowError as exc:
        print(f"工作流模板有问题：{exc}", file=sys.stderr)
        return 1
    except EngineError as exc:
        print(f"引擎没干成：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    print()
    print(f"去背完成，在 {made.path.parent}")
    print(f"  源图 {made.from_image}")
    print(f"  {made.id}  模型 {made.model}  {made.path.name}")
    return 0


def _run_stub(config: Config, args: argparse.Namespace) -> int:
    """起假引擎。没有显卡、权重还没下完的时候，靠它把工作台这一段走通。"""
    from .stub import StubEngine

    engine = StubEngine(
        host=config.comfy.host,
        port=config.comfy.port,
        mode="RGBA" if args.rgba else "RGB",
    ).start()
    print(f"假引擎已起：http://{engine.host}:{engine.port}")
    print("它不假装懂工作流 —— 收到什么 prompt 都收下，然后报「完成」并回一张小图。")
    print("Ctrl-C 停。")
    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        print()
    finally:
        engine.stop()
    return 0


def main(argv: list[str] | None = None, repo_root: Path | str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lab",
        description="鱼片加工厂 · 本地出片工作台（推理交给本机 ComfyUI）",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="检查环境：引擎、权重、节点、素材、输出目录")

    assets_parser = subparsers.add_parser("assets", help="素材索引与去重（只读引用）")
    assets_sub = assets_parser.add_subparsers(dest="assets_action")
    scan_parser = assets_sub.add_parser("scan", help="扫描素材根目录，写索引并报告")
    scan_parser.add_argument(
        "--verify-readonly",
        action="store_true",
        help="扫描前后核对素材 mtime/size，证明没动过它们（I/O 翻倍）",
    )
    assets_sub.add_parser("dupes", help="按已有索引列出重复组")

    make_parser = subparsers.add_parser(
        "make", help="给一句话，出一批图（写到 output/YYYY-MM-DD/）"
    )
    make_parser.add_argument(
        "--prompt", "-p", required=True, help="画什么。中文英文都行"
    )
    make_parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=None,
        help="出几张（默认取 [generate].count）",
    )
    make_parser.add_argument(
        "--seed",
        "-s",
        type=int,
        default=None,
        help="锁死种子；不给就随机（每张各记自己的）",
    )
    make_parser.add_argument(
        "--negative", default=None, help="负面词（默认取 [generate].negative）"
    )
    make_parser.add_argument("--steps", type=int, default=None, help="步数")
    make_parser.add_argument(
        "--cfg", type=float, default=None, help="引导强度（越大越贴提示词）"
    )
    make_parser.add_argument("--width", type=int, default=None, help="画布宽")
    make_parser.add_argument("--height", type=int, default=None, help="画布高")
    make_parser.add_argument(
        "--init-image",
        default=None,
        help="底图路径：给了它就走 img2img 模板（[workflows].img2img 那份），把形象钉在原来那张图上",
    )
    make_parser.add_argument(
        "--denoise",
        type=float,
        default=None,
        help="重绘强度 0–1（默认取 [generate].denoise，只在给了 --init-image 时有意义）：越大越自由，越小越像原图",
    )
    make_parser.add_argument(
        "--workflow",
        default=None,
        help="换这次用的模板（workflows/ 下的文件名；有底图换 img2img 那份，没有换文生图那份）",
    )

    sticker_parser = subparsers.add_parser(
        "sticker",
        help="把一张图去背成透明底贴纸（贴着原图落；外来的图落到 output/YYYY-MM-DD/）",
    )
    sticker_parser.add_argument(
        "image", help="要去背的图片路径：工作台出的图和别的图都行"
    )

    stub_parser = subparsers.add_parser(
        "stub", help="起一个假引擎（无显卡也能端到端调试）"
    )
    stub_parser.add_argument(
        "--rgba",
        action="store_true",
        help="回的图带透明通道 —— 调 lab sticker 时用它，不透明的图证明不了去背",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_usage()
        print("\n先用 `python -m lab doctor` 看看环境齐不齐。")
        return 2

    try:
        config = load_config(repo_root or REPO_ROOT)
    except ConfigError as exc:
        print(f"配置读不出来：{exc}", file=sys.stderr)
        return 2

    if args.command == "doctor":
        results = run_doctor(config)
        _print_report(results)
        return 0 if is_ready(results) else 1

    if args.command == "assets":
        # verify_readonly 只挂在 scan 子命令上，dupes 与「没写子命令」时没有这个属性
        return _run_assets(
            config, args.assets_action, getattr(args, "verify_readonly", False)
        )

    if args.command == "make":
        return _run_make(config, args)

    if args.command == "sticker":
        return _run_sticker(config, args)

    if args.command == "stub":
        return _run_stub(config, args)

    return 2
