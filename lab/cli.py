"""命令行入口。

    python -m lab doctor          检查环境齐不齐
    python -m lab assets scan     扫描素材，写索引，报张数与重复
    python -m lab assets dupes    按索引列出重复组
    python -m lab make            给一句话，出一批图（这是工作台的主要用途）
    python -m lab sticker <图片>  把一张图去背成透明底贴纸
    python -m lab caption <贴纸> --text "你好呀"  # glossary-ok: docs/roadmap.md:165
                                  给贴纸印一句台词，同目录同序号落成 .captioned.png
    python -m lab keep <图片>     把这张定稿搬进配方库（data/registry.json）
    python -m lab gallery         列出配方库里留了什么
    python -m lab serve           起本地界面：浏览器打开四个页面
    python -m lab stub            起一个假引擎（无显卡也能端到端调试）

任何子命令都能加 `--repo-root <目录>`（或设环境变量 `LAB_REPO_ROOT`）把工作台钉在
另一个仓库上：配置、output/、data/ 全按那里算。默认锚在**模块文件所在的那个仓库**
（不是当前目录）—— 所以要验一遍临时仓库，不指明的话出的图会照样写进真仓库。

退出码：0 = 全绿能干活；1 = 有 FAIL、有没查成的项、或有问题要处理；2 = 命令本身
用错了（参数不对、配置读不出来）。WARN 不算通过 —— 「没配所以没查」和「查了没
问题」是两回事，混在一起会在接线之前就把问题咽掉。

**顶层只 import 标准库 + doctor**：素材索引要 Pillow，所以它是在 `_run_assets`
里才 import 的。这样「没装 Pillow 的机器也能跑 `lab doctor`」不是一句口号。
"""

from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config, ConfigError, load_config
from .doctor import Status, is_ready, run_doctor

if TYPE_CHECKING:
    from .assets import Index
    from .registry import Entry

__all__ = ["main"]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 不写 `--repo-root` 时看这个环境变量。子进程里（测试、脚本）比命令行参数省事，
#: 也让"临时仓库"这件事在所有子命令上一致地生效。
REPO_ROOT_ENV = "LAB_REPO_ROOT"

#: 每个子命令都认的 `--repo-root`。
#: 用 `default=SUPPRESS`：不给就压根不设这个属性，于是子命令解析不会把顶层已经
#: 解析到的值抹成 None —— 否则 `lab --repo-root X gallery` 会被子命令的默认值盖掉。
_REPO_ROOT_OPTION = argparse.ArgumentParser(add_help=False)
_REPO_ROOT_OPTION.add_argument(
    "--repo-root",
    default=argparse.SUPPRESS,
    metavar="目录",
    help="把工作台钉在这个仓库上（配置、output/、data/ 都按它算）；不给就看环境变量 LAB_REPO_ROOT",
)

BADGE = {Status.OK: "[ OK ]", Status.WARN: "[WARN]", Status.FAIL: "[FAIL]"}
RULE_WIDTH = 60  # 报告横线的默认宽度；表格比这宽时按表宽画
LABEL_WIDTH = 14  # 按显示宽度算，所以中英文标签都能对齐
KIND_LABEL = {"exact": "字节一致", "perceptual": "画面近似", "suspect": "疑似同图"}


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度右补空格 —— 一个汉字占两格，不能用 str.ljust。"""
    return text + " " * max(0, width - _display_width(text))


def _shorten(text: str, width: int) -> str:
    """超宽就截断加省略号 —— 按显示宽度算，一个汉字占两格。

    表格要能一眼扫完，宁可截掉长模型名尾巴也别让整张表横向跑出去；完整的那份
    就在 data/registry.json 里，要看原样去读文件。
    """
    if _display_width(text) <= width:
        return text
    kept = ""
    for ch in text:
        # 少留一格给省略号：它自己占一格，按整宽截会顶出去一格。
        if _display_width(kept + ch) > width - 1:
            break
        kept += ch
    return kept + "…"


def _wrap(text: str, width: int) -> list[str]:
    """按显示宽度折行，一个汉字算两格 —— 收尾那几句话比横线长时折开。

    只在空格、标点之后优先断：中文没有词间空格，硬按宽度切会切在词中间。
    断不开就退回硬切，总比让横线短一截、字甩到下边强。
    """
    if width <= 0:
        return [text]
    pieces: list[str] = []
    line = ""
    for ch in text:
        if _display_width(line + ch) > width:
            cut = max(line.rfind("、"), line.rfind("，"), line.rfind("。"), line.rfind(" "))
            if cut > 0:
                pieces.append(line[: cut + 1])
                line = line[cut + 1 :] + ch
            else:
                pieces.append(line)
                line = ch
        else:
            line += ch
    if line:
        pieces.append(line)
    return pieces


def _section(
    title: str,
    lines: list[str],
    footer: str | list[str] | None = None,
    *,
    width: int = RULE_WIDTH,
) -> None:
    """一段报告：标题、横线、正文、（可选）横线 + 收尾。

    横线默认画 60 格；表格比这宽时按表宽画，否则表格会从横线底下漏出去。
    收尾可以是几行（`_wrap` 折好的），横线宽度始终照 `width` —— 比正文窄的横线
    看起来像没画完，比它宽的字会甩出去。
    """
    rule = "─" * width
    print()
    print(title)
    print(rule)
    for line in lines:
        print(line)
    if footer is not None:
        print(rule)
        for line in [footer] if isinstance(footer, str) else footer:
            print(line)
    print(rule)
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
        print(f"[{index}/{total}] 生成中 · 种子 {seed}", file=self._stream, flush=True)

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


def _run_lettering(config: Config, args: argparse.Namespace) -> int:
    """加字那一站：贴纸进去，带台词成图出来（票 #10；子命令名见 docs/roadmap.md:165）。

    加字是纯本地的图像动作，不经过引擎：没有 ComfyUI 也能跑。
    """
    from .lettering import LetteringError, letter

    try:
        made = letter(config, args.image, args.text)
    except ConfigError as exc:
        # 配置错和"命令用错了"一样，都得先改一行再重来 —— 归到 2。
        print(f"配置有问题：{exc}", file=sys.stderr)
        return 2
    except LetteringError as exc:
        print(f"加字不成：{exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    print()
    print(f"加字完成，在 {made.path.parent}")
    print(f"  源图 {made.from_image}")
    print(f"  {made.id}  {made.path.name}")
    for line in made.lines:
        print(f"  | {line}")
    return 0


#: 配方库表格的列宽（按显示宽度算）。模型名最容易超，给最长，其余刚好够放。
GALLERY_COLUMNS = (("号", 10), ("阶段", 9), ("模型", 26), ("种子", 11), ("留的时间", 17))


def _gallery_width() -> int:
    """表格实际占的格数 —— 列宽之和 + 列间空格。横线照这个画。"""
    return sum(width for _, width in GALLERY_COLUMNS) + len(GALLERY_COLUMNS) - 1


def _gallery_row(cells: dict[str, str]) -> str:
    """一行表格 —— 每格先按列宽截再补空格，长名字不会把后面的列顶歪。"""
    return " ".join(
        _pad(_shorten(cells.get(title, ""), width), width)
        for title, width in GALLERY_COLUMNS
    )


def _when(stamp: str) -> str:
    """把 ISO 时间裁成表里放得下的那一截：`2026-10-24 02:05`。

    秒和时区对"什么时候留的"没有信息量，留在 JSON 里就够了。
    """
    return stamp.replace("T", " ")[:16] if stamp else "—"


def _print_gallery(entries: list[Entry]) -> None:
    if not entries:
        _section(
            "鱼片加工厂 · 配方库",
            ["还没留东西。"],
            "挑中一张就 `python -m lab keep <图片>`，它就从当天的账里搬过来。",
        )
        return

    lines = [_gallery_row({title: title for title, _ in GALLERY_COLUMNS})]
    for one in entries:
        lines.append(
            _gallery_row(
                {
                    "号": one.id,
                    "阶段": one.kind,
                    "模型": one.model or "—",
                    "种子": str(one.seed),
                    "留的时间": _when(one.created_at),
                }
            )
        )
    width = _gallery_width()
    _section(
        "鱼片加工厂 · 配方库",
        lines,
        _wrap(
            f"{len(entries)} 条，库里就是这些。"
            "时间一栏是这一条落账的时刻：工作台出的图记生成那一刻，"
            "外来的图记搬进库那一刻。要照某一条重跑："
            "python -m lab reproduce <号>。",
            width,
        ),
        width=width,
    )


def _run_keep(config: Config, args: argparse.Namespace) -> int:
    """`lab keep` —— 挑中的那张从当天账里搬进配方库（票 #11）。

    全量记录里全是废图与半成品，配方库只收定稿；"保留"就是这一条命令做的事。
    """
    from .keeping import keep
    from .registry import library_path

    try:
        entry, traced = keep(config, args.image)
    except ValueError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2

    library = library_path(config.repo_root)
    print()
    if traced:
        print(f"已留：{entry.id}.{entry.kind}  ←  当天的记录")
    else:
        # 说实话而不是假装：没有来路的那条，将来重不出同一张，得让人当场知道。
        print(f"已留：{entry.id}.{entry.kind}  ←  当天账里没有它的来路")
        print("  生成参数那一栏是空的（种子 0、模型空）—— 这张不是工作台出的，")
        print("  或者账被清走了。要能重跑，得留着出它的那次记录。")
    print(f"  落点 {entry.file}")
    print(f"  配方库 {library}")
    return 0


def _run_gallery(config: Config, args: argparse.Namespace) -> int:
    """`lab gallery` —— 配方库里留了什么，一张表看完（票 #11）。"""
    from .keeping import gallery

    try:
        entries = gallery(config)
    except ValueError as exc:
        print(f"配方库读不出来：{exc}", file=sys.stderr)
        return 2
    _print_gallery(entries)
    return 0


def _run_reproduce(config: Config, args: argparse.Namespace) -> int:
    """`lab reproduce` —— 照配方库里的那条再跑一次，看还能不能出回原来那张（票 #12）。"""
    from .comfy import EngineError
    from .reproduce import reproduce
    from .workflows import WorkflowError

    try:
        report = reproduce(config, args.id, progress=_MakeProgress())
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
    print(f"重跑完成，在 {report.image.path.parent}")
    print(f"  原配方 {report.recipe.id}  →  {report.image.id}")
    print(f"  seed={report.image.spec.seed}  {report.image.path.name}")
    if report.same is True:
        print("  与原来那张逐像素一致。")
    elif report.same is False:
        print("  与原来那张不一样。")
        print("  两张都留着，比一比差在哪儿 —— 换过模板、换过模型、换过引擎版本都会这样。")
    else:
        print("  没有原图可比 —— 这次只验了它还能跑出片。")
    if report.note:
        print(f"  {report.note}")
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


def _run_serve(config: Config, args: argparse.Namespace) -> int:
    """起本地界面，一直服务到 Ctrl-C。

    FastAPI 与 uvicorn 只在这条路上 import —— 别的子命令不该为界面买单
    （顶层只留标准库 + doctor，见文件头那条约定）。
    """
    from .web import serve

    return serve(config, host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    """搭出整棵命令树。

    单独一个函数只为一件事：让测试能遍历它。`--repo-root` 得挂在**每个**子命令上
    （`lab make --repo-root X` 与 `lab --repo-root X make` 是两种肌肉记忆），漏挂一个
    就有一半写法失效 —— 而这种漏是靠人记不住的，靠遍历断言才守得住。
    """
    parser = argparse.ArgumentParser(
        prog="lab",
        description="鱼片加工厂 · 本地出片工作台（推理交给本机 ComfyUI）",
        parents=[_REPO_ROOT_OPTION],
    )
    subparsers = parser.add_subparsers(dest="command")
    # parents=[_REPO_ROOT_OPTION] 挂在每个子命令上：`lab --repo-root X make` 与
    # `lab make --repo-root X` 都得能用，写命令的时候不该还要记参数摆哪儿。
    subparsers.add_parser(
        "doctor",
        parents=[_REPO_ROOT_OPTION],
        help="检查环境：引擎、权重、节点、素材、输出目录",
    )

    assets_parser = subparsers.add_parser(
        "assets", parents=[_REPO_ROOT_OPTION], help="素材索引与去重（只读引用）"
    )
    assets_sub = assets_parser.add_subparsers(dest="assets_action")
    scan_parser = assets_sub.add_parser(
        "scan", parents=[_REPO_ROOT_OPTION], help="扫描素材根目录，写索引并报告"
    )
    scan_parser.add_argument(
        "--verify-readonly",
        action="store_true",
        help="扫描前后核对素材 mtime/size，证明没动过它们（I/O 翻倍）",
    )
    assets_sub.add_parser(
        "dupes", parents=[_REPO_ROOT_OPTION], help="按已有索引列出重复组"
    )

    make_parser = subparsers.add_parser(
        "make",
        parents=[_REPO_ROOT_OPTION],
        help="给一句话，出一批图（写到 output/YYYY-MM-DD/）",
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
        help=(
            "底图路径：给了它就走 img2img 模板（[workflows].img2img 那份），"
            "把形象钉在原来那张图上"
        ),
    )
    make_parser.add_argument(
        "--denoise",
        type=float,
        default=None,
        help=(
            "重绘强度 0–1（默认取 [generate].denoise，只在给了 --init-image 时有意义）："
            "越大越自由，越小越像原图"
        ),
    )
    make_parser.add_argument(
        "--workflow",
        default=None,
        help="换这次用的模板（workflows/ 下的文件名；有底图换 img2img 那份，没有换文生图那份）",
    )

    sticker_parser = subparsers.add_parser(
        "sticker",
        parents=[_REPO_ROOT_OPTION],
        help="把一张图去背成透明底贴纸（贴着原图落；外来的图落到 output/YYYY-MM-DD/）",
    )
    sticker_parser.add_argument(
        "image", help="要去背的图片路径：工作台出的图和别的图都行"
    )

    # glossary-ok: caption 是 docs/roadmap.md:165 定死的对外子命令名，改不了
    lettering_parser = subparsers.add_parser(
        "caption",  # glossary-ok: 子命令名，docs/roadmap.md:165 定死的对外接口
        parents=[_REPO_ROOT_OPTION],
        help="给一张贴纸印一句台词（同目录同序号落成 .captioned.png；不需要引擎）",
    )
    lettering_parser.add_argument(
        "image", help="要加字的贴纸路径：一般就是 lab sticker 刚出的那张"
    )
    lettering_parser.add_argument(
        "--text",
        "-t",
        required=True,
        help="要印上去的台词：太长会自动折行，压在下沿居中",
    )

    keep_parser = subparsers.add_parser(
        "keep",
        parents=[_REPO_ROOT_OPTION],
        help="把这张定稿搬进配方库（从当天的全量记录里抄参数，进 git 的那本就是它）",
    )
    keep_parser.add_argument(
        "image", help="要保留的那张图：可以是原图、贴纸、带字成图里的任意一张"
    )

    subparsers.add_parser(
        "gallery",
        parents=[_REPO_ROOT_OPTION],
        help="列出配方库里留了什么（号 / 阶段 / 模型 / 种子 / 时间）",
    )

    reproduce_parser = subparsers.add_parser(
        "reproduce",
        parents=[_REPO_ROOT_OPTION],
        help="照配方库里的那条重跑一次，与原图逐像素比（落到今天那个目录）",
    )
    reproduce_parser.add_argument(
        "id", help="配方库里的号：python -m lab gallery 里「号」那一列"
    )

    stub_parser = subparsers.add_parser(
        "stub", parents=[_REPO_ROOT_OPTION], help="起一个假引擎（无显卡也能端到端调试）"
    )
    stub_parser.add_argument(
        "--rgba",
        action="store_true",
        help="回的图带透明通道 —— 调 lab sticker 时用它，不透明的图证明不了去背",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        parents=[_REPO_ROOT_OPTION],
        help="起本地界面：浏览器打开四个页面（地址端口取 [server]，命令行给的覆盖它）",
    )
    serve_parser.add_argument(
        "--host", default=None, help="监听地址（默认取 [server].host）"
    )
    serve_parser.add_argument(
        "--port", type=int, default=None, help="端口（默认取 [server].port）"
    )

    return parser


def main(argv: list[str] | None = None, repo_root: Path | str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_usage()
        print("\n先用 `python -m lab doctor` 看看环境齐不齐。")
        return 2

    # 钉在哪个仓库上：命令行 > 函数入参（测试与脚本用）> 环境变量 > 模块所在仓库。
    # 最后那个兜底锚的是**模块文件的位置**，不是当前目录 —— 所以 `cd` 到别处跑
    # `python -m lab make` 照样写进真仓库，想验临时仓库就得明说。
    chosen = (
        getattr(args, "repo_root", None) or repo_root or os.environ.get(REPO_ROOT_ENV)
    )
    try:
        config = load_config(chosen or REPO_ROOT)
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

    if args.command == "caption":  # glossary-ok: 子命令名，与上面的 add_parser 对应
        return _run_lettering(config, args)

    if args.command == "keep":
        return _run_keep(config, args)

    if args.command == "gallery":
        return _run_gallery(config, args)

    if args.command == "reproduce":
        return _run_reproduce(config, args)

    if args.command == "serve":
        return _run_serve(config, args)

    if args.command == "stub":
        return _run_stub(config, args)

    return 2
