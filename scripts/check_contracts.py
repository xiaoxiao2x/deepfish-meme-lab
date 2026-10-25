#!/usr/bin/env python
"""两边对账：模块的 __all__ 与真的在用它的人。

三类，都是「代码里有两处各说各的，没人比」：

1. ghost：__all__ 里列了、模块里拿不出来的名字。那份清单在吹牛。
2. miss：from .x import name 而 name 不在 x.__all__ 里。契约没被认，
   今天正好能跑，改天 x 一改名就轮到别人报 AttributeError。
3. private：tests/ 之间 from tests.x import _name。带下划线的名字是那个文件
   自己的，跨文件借走之后签名一变就静默崩 —— 与 showcase.picture_at 改名那次
   同一类事故，只是这次跨了文件。

为什么自己做而不是开 ruff 规则：PLC2701（禁导私有名）要 preview 才生效，
实测只打一行 warning 说这条选择不生效；而 __all__ 与 import 的一致性要跨
文件看两处，没有任何现成 linter 做这件事。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LAB = REPO_ROOT / "lab"
TESTS = REPO_ROOT / "tests"


def module_names(tree: ast.Module) -> set[str]:
    """这个模块顶格能拿到的名字：定义、赋值、以及 import 进来的（转手来的也算）。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def dunder_all(tree: ast.Module) -> set[str] | None:
    """模块的 __all__。没写就是 None（这份检查管不着），写坏了也当 None。"""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                return None
            if isinstance(value, list | tuple):
                return {str(item) for item in value}
            return None
    return None


def ghosts(trees: dict[str, ast.Module], exported: dict[str, set[str] | None]) -> list[str]:
    problems: list[str] = []
    for stem, names in sorted(exported.items()):
        if names is None:
            continue
        have = module_names(trees[stem])
        for name in sorted(names - have):
            problems.append(
                f"lab/{stem}.py: __all__ 列了 {name}，模块里拿不出这个名字"
                " —— 要么定义它，要么从清单里删掉"
            )
    return problems


def misses(trees: dict[str, ast.Module], exported: dict[str, set[str] | None]) -> list[str]:
    problems: list[str] = []
    for stem, tree in sorted(trees.items()):
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1:
                continue
            if node.module not in exported:
                continue
            names = exported[node.module]
            if names is None:
                continue
            for alias in node.names:
                if alias.name not in names:
                    problems.append(
                        f"lab/{stem}.py:{node.lineno} from .{node.module} import {alias.name}"
                        f" —— {alias.name} 不在 lab/{node.module}.py 的 __all__ 里"
                    )
    return problems


def private_test_imports() -> list[str]:
    problems: list[str] = []
    published = {
        path.stem: dunder_all(ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(TESTS.glob("*.py"))
    }
    for path in sorted(TESTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("tests."):
                continue
            owner = node.module.split(".")[-1]
            listed = published.get(owner)
            for alias in node.names:
                if not alias.name.startswith("_"):
                    continue
                if listed is not None and alias.name in listed:
                    continue
                problems.append(
                    f"tests/{path.name}:{node.lineno} from {node.module} import {alias.name}"
                    " —— 带下划线的是那个文件的私有名，跨文件借走要改成公开名"
                )
    return problems


def main() -> int:
    trees = {
        path.stem: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(LAB.glob("*.py"))
    }
    exported = {stem: dunder_all(tree) for stem, tree in trees.items()}
    problems = ghosts(trees, exported) + misses(trees, exported) + private_test_imports()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"\n契约对不上：上面 {len(problems)} 处。__all__ 是这一层对外的说法，"
            "改了模块就把清单一起改。",
            file=sys.stderr,
        )
        return 1
    listed = sum(1 for names in exported.values() if names is not None)
    print(
        f"契约对得上：lab/ 的 {listed} 份 __all__ 都用得上、导得出，"
        "tests/ 之间没有借私有名。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
