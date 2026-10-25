#!/usr/bin/env python
"""两边对账：模块的 __all__ 与真的在用它的人。

六类，都是「代码里有两处各说各的，没人比」：

1. ghost：__all__ 里列了、模块里拿不出来的名字。那份清单在吹牛。
2. miss：from .x import name 而 name 不在 x.__all__ 里。契约没被认，
   今天正好能跑，改天 x 一改名就轮到别人报 AttributeError。
3. private：tests/ 之间 from tests.x import _name。带下划线的名字是那个文件
   自己的，跨文件借走之后签名一变就静默崩 —— 与 showcase.picture_at 改名那次
   同一类事故，只是这次跨了文件。

4. snapshot：读了 config 却没自己把它取出来的函数。web.py 的路由都住在
   create_app 的闭包里，直接读外层那个 config 读到的是进程启动时的快照 ——
   设置页刚存下的引擎地址当场不生效，而页面看着好好的。
5. address：现拼「host:port」。给人看的那种只有 comfy.show_address 一处
   （页面输入框、体检、报错共用），带协议的那种只有 config.ComfyConfig.endpoint。
6. clash：类方法与嵌套函数同名。按 `def 名字` 定位插入点的脚本会把行插进另一个
   同名 def（CardView.thumb 与路由 thumb 就是这么撞的），改名比事后翻 diff 便宜。

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


# ── 后三类：隐形契约，两处各说各的但长得不像「对不上」 ──


def _own_scope(func: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[bool, list[int]]:
    """这个函数自己绑定了 config 吗；它读了哪些行的 config（不含嵌套 def/class 里的）。"""
    bound = False
    loaded: list[int] = []
    slots = [
        *func.args.posonlyargs,
        *func.args.args,
        *func.args.kwonlyargs,
        func.args.vararg,
        func.args.kwarg,
    ]
    if any(slot is not None and slot.arg == "config" for slot in slots):
        bound = True

    def visit(node: ast.AST) -> None:
        nonlocal bound
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef | ast.Lambda):
                continue
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if isinstance(child, ast.Name) and child.id == "config":
                if isinstance(child.ctx, ast.Store):
                    bound = True
                else:
                    loaded.append(child.lineno)
            visit(child)

    for statement in func.body:
        visit(statement)
    return bound, loaded


def _nested_functions(func: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """这个函数体里的嵌套 def（类里的不算）。"""
    found: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for child in ast.iter_child_nodes(func):
        if isinstance(child, ast.ClassDef):
            continue
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append(child)
            continue
        found.extend(_nested_functions(child))
    return found


def _check_snapshot(
    stem: str, func: ast.FunctionDef | ast.AsyncFunctionDef, problems: list[str]
) -> None:
    bound, loaded = _own_scope(func)
    if bound:
        return  # 这一层自己取过 settings.config，整个子树都安全
    if loaded:
        problems.append(
            f"lab/{stem}.py:{loaded[0]} {func.name} 读了外层的 config"
            " —— 那是启动时的快照，刚存下的值不生效"
            "；在函数第一句写 config = settings.config"
        )
        return  # 同一个根因，往下的嵌套不重复报
    for child in _nested_functions(func):
        _check_snapshot(stem, child, problems)


def snapshots(trees: dict[str, ast.Module]) -> list[str]:
    """读了外层 config 的函数 —— 那是启动时的快照，页面刚存下的值当场不生效。"""
    problems: list[str] = []
    for stem, tree in sorted(trees.items()):
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                _check_snapshot(stem, node, problems)
    return problems


# 地址的定义处：给人看的一种，带协议的三处（各自代表一类服务）。别处要地址就调它们。
ADDRESS_SOURCES = {
    ("comfy", "show_address"),
    ("comfy", "base"),
    ("config", "endpoint"),
    ("config", "address"),
    ("stub", "url"),
}


def _f_strings(tree: ast.Module) -> list[tuple[str, ast.JoinedStr]]:
    """每个 f-string，连同它所在的那个函数名（模块级为空串）。"""
    found: list[tuple[str, ast.JoinedStr]] = []

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = child.name
            else:
                name = owner
            if isinstance(child, ast.JoinedStr):
                found.append((name, child))
            walk(child, name)

    walk(tree, "")
    return found


def _address_slots(node: ast.JoinedStr) -> set[str]:
    """这段 f-string 里，直接读 .host / .port 的插值各有哪些。"""
    found: set[str] = set()
    for value in node.values:
        if not isinstance(value, ast.FormattedValue):
            continue
        expr = value.value
        if isinstance(expr, ast.Attribute) and expr.attr in ("host", "port"):
            found.add(expr.attr)
        elif isinstance(expr, ast.Name) and expr.id in ("host", "port"):
            found.add(expr.id)
    return found


def address_spellings(trees: dict[str, ast.Module]) -> list[str]:
    """现拼 host 与 port 的 f-string —— 地址的定义处就那么几处，别处要调它们。"""
    problems: list[str] = []
    for stem, tree in sorted(trees.items()):
        for owner, node in _f_strings(tree):
            if (stem, owner) in ADDRESS_SOURCES:
                continue
            if _address_slots(node) >= {"host", "port"}:
                problems.append(
                    f"lab/{stem}.py:{node.lineno} 在 {owner or '模块级'} 里现拼了引擎地址"
                    " —— 给人看的走 comfy.show_address，带协议的走 endpoint / base / url"
                )
    return problems


def _functions_by_kind(tree: ast.Module) -> tuple[dict[str, int], dict[str, int]]:
    """（类方法名 → 行，函数里的嵌套 def 名 → 行）。模块级 def 两边都不进。"""
    methods: dict[str, int] = {}
    nested: dict[str, int] = {}

    def walk(node: ast.AST, in_class: bool, depth: int) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, True, depth)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                if in_class and depth == 0:
                    methods.setdefault(child.name, child.lineno)
                elif depth > 0:
                    nested.setdefault(child.name, child.lineno)
                walk(child, False, depth + 1)
            else:
                walk(child, in_class, depth)

    walk(tree, False, 0)
    return methods, nested


def clashing_names(trees: dict[str, ast.Module]) -> list[str]:
    """类方法与嵌套函数同名 —— 按 `def 名字` 定位的脚本会插错地方。"""
    problems: list[str] = []
    for stem, tree in sorted(trees.items()):
        methods, nested = _functions_by_kind(tree)
        for name in sorted(set(methods) & set(nested)):
            problems.append(
                f"lab/{stem}.py:{nested[name]} 的嵌套函数 {name} 与"
                f" 第 {methods[name]} 行的方法同名"
                " —— 按 def 名字定位的脚本会插错地方，改掉一个名字"
            )
    return problems


def main() -> int:
    trees = {
        path.stem: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(LAB.glob("*.py"))
    }
    exported = {stem: dunder_all(tree) for stem, tree in trees.items()}
    problems = (
        ghosts(trees, exported)
        + misses(trees, exported)
        + private_test_imports()
        + snapshots(trees)
        + address_spellings(trees)
        + clashing_names(trees)
    )
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            f"\n对不上：上面 {len(problems)} 处。__all__ 是这一层对外的说法，"
            "改了模块就把清单一起改；后三条隐形契约同理。",
            file=sys.stderr,
        )
        return 1
    listed = sum(1 for names in exported.values() if names is not None)
    print(
        f"契约对得上：lab/ 的 {listed} 份 __all__ 都用得上、导得出，"
        "tests/ 之间没有借私有名，路由都现取 config，地址都在那几处定义里，"
        "类方法与嵌套函数不重名。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
