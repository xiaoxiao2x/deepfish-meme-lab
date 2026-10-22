"""命令行入口。

    python -m lab doctor          检查环境齐不齐
    python -m lab assets scan     扫描素材，写索引，报张数与重复
    python -m lab assets dupes    按索引列出重复组

退出码：0 = 全绿能干活；1 = 有 FAIL、有没查成的项、或有问题要处理；2 = 命令本身
用错了（参数不对、配置读不出来）。WARN 不算通过 —— 「没配所以没查」和「查了没
问题」是两回事，混在一起会在接线之前就把问题咽掉。
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

from .assets import Index, build_index, load_index, save_index
from .config import Config, ConfigError, load_config
from .doctor import Status, is_ready, run_doctor

__all__ = ["main"]

REPO_ROOT = Path(__file__).resolve().parent.parent

BADGE = {Status.OK: "[ OK ]", Status.WARN: "[WARN]", Status.FAIL: "[FAIL]"}
RULE = "─" * 60
LABEL_WIDTH = 14  # 按显示宽度算，所以中英文标签都能对齐


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度右补空格 —— 一个汉字占两格，不能用 str.ljust。"""
    return text + " " * max(0, width - _display_width(text))


def _print_report(results) -> None:
    print()
    print("鱼片加工厂 · 环境体检")
    print(RULE)
    for result in results:
        print(f"{BADGE[result.status]} {_pad(result.label, LABEL_WIDTH)} {result.detail}")
    print(RULE)

    fails = sum(1 for r in results if r.status is Status.FAIL)
    warns = sum(1 for r in results if r.status is Status.WARN)
    if fails:
        print(f"{fails} 项没通过，{warns} 项待办 —— 先解决上面的 FAIL。")
    elif warns:
        print(f"{warns} 项还没配、没查 —— 引擎能跑不等于工作台能干活。")
    else:
        print("全绿。")
    print()


def _print_assets_report(index: Index, index_path: Path) -> None:
    animated = sum(1 for a in index.assets if a.animated)
    exact = sum(1 for g in index.duplicates if g.kind == "exact")
    perceptual = sum(1 for g in index.duplicates if g.kind == "perceptual")

    print()
    print("鱼片加工厂 · 素材索引")
    print(RULE)
    rows = [
        ("根目录", f"{len(index.roots)} 个"),
        ("张数", f"{index.count} 张（其中动图 {animated}）"),
        ("重复", f"{len(index.duplicates)} 组（字节一致 {exact}、画面近似 {perceptual}）"),
        ("索引", str(index_path)),
    ]
    for label, value in rows:
        print(f"{_pad(label, LABEL_WIDTH)} {value}")
    print(RULE)
    for problem in index.problems:
        print(f"[注意] {problem}")
    if index.problems:
        print(RULE)
    print()


def _print_dupes(index: Index) -> None:
    print()
    print("鱼片加工厂 · 重复素材")
    print(RULE)
    if not index.duplicates:
        print("没发现重复。")
    for position, group in enumerate(index.duplicates, start=1):
        kind = "字节一致" if group.kind == "exact" else "画面近似"
        print(f"[{position}] {kind} · {len(group.paths)} 张")
        for path in group.paths:
            print(f"      {path}")
    print(RULE)
    print()


def _run_assets(config: Config, action: str | None) -> int:
    index_path = config.paths.asset_index

    if action == "dupes":
        if not index_path.exists():
            print(
                f"还没有索引：{index_path}\n先跑 `python -m lab assets scan`。",
                file=sys.stderr,
            )
            return 2
        _print_dupes(load_index(index_path))
        return 0

    if action != "scan":
        print("用法：python -m lab assets scan|dupes", file=sys.stderr)
        return 2

    index = build_index(config.paths.asset_roots)
    save_index(index, index_path)
    _print_assets_report(index, index_path)
    return 1 if index.problems else 0


def main(argv: list[str] | None = None, repo_root: Path | str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lab",
        description="鱼片加工厂 · 本地出片工作台（推理交给本机 ComfyUI）",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="检查环境：引擎、权重、节点、素材、输出目录")

    assets_parser = subparsers.add_parser("assets", help="素材索引与去重（只读引用）")
    assets_sub = assets_parser.add_subparsers(dest="assets_action")
    assets_sub.add_parser("scan", help="扫描素材根目录，写索引并报告")
    assets_sub.add_parser("dupes", help="按已有索引列出重复组")

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
        return _run_assets(config, args.assets_action)

    return 2
