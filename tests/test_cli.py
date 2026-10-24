"""命令树的形状：`--repo-root` 得挂在每一个子命令上。

这不是测某个功能，是测「有没有漏挂」。人可以记住"加个参数"，记不住
"六个子命令、两个二级子命令一个都别落" —— 漏一个的表现是某一种写法
突然不认识这个参数，而跑的人只会以为自己敲错了地方。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lab.cli import build_parser


def _children(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """这一层的子命令。argparse 没有公开的遍历接口，只碰这两个最稳的字段。"""
    for action in parser._actions:  # noqa: SLF001 —— 没有公开替代，且只在测试里用
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return choices
    return {}


def _walk(
    parser: argparse.ArgumentParser, trail: tuple[str, ...]
) -> list[tuple[str, argparse.ArgumentParser]]:
    found = [((" ".join(trail) or "lab"), parser)]
    for name, sub in _children(parser).items():
        found.extend(_walk(sub, (*trail, name)))
    return found


def test_every_subcommand_takes_repo_root() -> None:
    """`lab make --repo-root X` 与 `lab --repo-root X make` 得一样能写。

    参数摆哪儿是肌肉记忆，而"这条命令支持、那条不支持"是最难记的一种不一致。
    """
    missing = [
        name
        for name, parser in _walk(build_parser(), ())
        if "--repo-root" not in parser.format_help()
    ]

    assert missing == [], f"这些子命令不认 --repo-root：{missing}"


def test_repo_root_is_not_defaulted_to_none() -> None:
    """不给 `--repo-root` 时压根不该设这个属性。

    用 `default=None` 会把顶层已经解析到的值抹掉：`lab --repo-root X gallery`
    走到子命令解析时又填一次 None，于是"指了仓库"这件事静默失效。所以这里
    要的是 SUPPRESS —— 不给就不存在。
    """
    args = build_parser().parse_args(["gallery"])

    assert not hasattr(args, "repo_root")


def test_repo_root_lands_wherever_it_is_written(tmp_path: Path) -> None:
    parser = build_parser()

    before = parser.parse_args(["--repo-root", str(tmp_path), "gallery"])
    after = parser.parse_args(["gallery", "--repo-root", str(tmp_path)])

    assert before.repo_root == str(tmp_path)
    assert after.repo_root == str(tmp_path)
