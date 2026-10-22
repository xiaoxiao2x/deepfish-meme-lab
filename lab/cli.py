"""命令行入口。

    python -m lab doctor      检查环境齐不齐

退出码：0 = 全绿能干活；1 = 有 FAIL 或有没查成的项；2 = 命令本身用错了
（参数不对、配置读不出来）。WARN 不算通过 —— 「没配所以没查」和「查了没问题」
是两回事，混在一起会在接线之前就把问题咽掉。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .doctor import Status, is_ready, run_doctor

__all__ = ["main"]

REPO_ROOT = Path(__file__).resolve().parent.parent

BADGE = {Status.OK: "[ OK ]", Status.WARN: "[WARN]", Status.FAIL: "[FAIL]"}
RULE = "─" * 60
LABEL_WIDTH = 14  # 够放下最长的那个标签（"custom_nodes" 时代的余量）


def _print_report(results) -> None:
    print()
    print("鱼片加工厂 · 环境体检")
    print(RULE)
    for result in results:
        print(f"{BADGE[result.status]} {result.label:<{LABEL_WIDTH}} {result.detail}")
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


def main(argv: list[str] | None = None, repo_root: Path | str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lab",
        description="鱼片加工厂 · 本地出片工作台（推理交给本机 ComfyUI）",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("doctor", help="检查环境：引擎、权重、节点、素材、输出目录")

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

    return 2
