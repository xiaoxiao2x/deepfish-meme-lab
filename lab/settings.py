"""设置页写到磁盘的那一层：把这几个可改的项写回 `config/local.toml`。

`config/local.toml` 是手写的 —— 有注释、有顺序、有别的键（`root`、`font`）。所以写回是
**就地改**：只在指定的那一段里替换那一行，别处一个字节不动。整本重写会把注释洗掉，而注释
正是这个文件里唯一解释「为什么这么配」的东西。

改完谁来重读：`Settings` 拿着当下的一份 `Config`，写完立刻 `load_config` 再读一遍 ——
页面与命令行读的因此始终是同一份（docs/design.md §9.3 ④）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .config import Config, load_config

__all__ = [
    "Settings",
    "THEME_LABELS",
    "parse_address",
    "workflow_choices",
    "write_local",
]

#: 主题的两档：值（写进 `[ui] theme` 的那一个）+ 页面上的说法。校验与页面都从这儿取。
THEME_LABELS = (("light", "亮"), ("dark", "暗"))

_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def parse_address(text: str, default_port: int) -> tuple[str, int]:
    """把人敲的引擎地址拆成 host 与 port。

    `127.0.0.1`、`127.0.0.1:8188`、`http://127.0.0.1:8188/` 三种写法都收 ——
    从别处粘一整条地址进来是常事。没写端口就用 default_port。
    """
    raw = text.strip()
    if not raw:
        raise ValueError("引擎地址是空的 —— 填一个主机，比如 127.0.0.1:8188。")
    if "://" in raw:
        scheme, _, raw = raw.partition("://")
        if scheme not in ("http", "https"):
            raise ValueError(
                f"不认这个写法：{text} —— 只收 http:// 或 https://，或者干脆只写主机与端口。"
            )
    raw = raw.rstrip("/")
    if "/" in raw:
        raise ValueError(f"地址里不该有路径：{text} —— 引擎的接口都在根上，填 host:port 就行。")
    host, sep, port_text = raw.rpartition(":")
    if not sep:
        host, port_text = raw, ""
    if not host:
        raise ValueError(f"地址里没有主机：{text} —— 填一个主机，比如 127.0.0.1:8188。")
    return host, _port_of(port_text, text) if port_text else default_port


def _port_of(port_text: str, text: str) -> int:
    if not port_text.isdigit():
        raise ValueError(f"端口不是数字：{text} —— 填 1 到 65535 之间的一个数。")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError(f"端口超出范围：{text} —— 填 1 到 65535 之间的一个数。")
    return port


def workflow_choices(repo_root: Path | str) -> tuple[str, ...]:
    """`workflows/` 里现有的模板名，排好序。设置页那个下拉与校验都从这儿取。"""
    folder = Path(repo_root) / "workflows"
    return tuple(sorted(path.name for path in folder.glob("*.json")))


class Settings:
    """页面能改的那几项，外加「改完谁重读」。

    每个 set_* 走同一条路：校验 → 就地写回 local.toml → 重新读一份 Config。
    读不回来就还原文件 —— 写坏了也不把好好的配置留在半路上。
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    @property
    def config(self) -> Config:
        """当下的这一份。每个请求开头重新取一次，所以改完下一句就读到新的。"""
        return self._config

    def local_path(self) -> Path:
        """这两层配置里不进 git 的那一份。"""
        return self._config.repo_root / "config" / "local.toml"

    def set_engine(self, address: str) -> Config:
        host, port = parse_address(address, self._config.comfy.port)
        return self._apply({"comfy": {"host": host, "port": port}})

    def set_workflow(self, name: str) -> Config:
        choices = workflow_choices(self._config.repo_root)
        if name not in choices:
            known = "、".join(choices) if choices else "（workflows/ 里一个模板都没有）"
            raise ValueError(f"不认识这个工作流：{name} —— 挑一个现有的：{known}。")
        return self._apply({"workflows": {"default": name}})

    def set_paths(self, *, asset_roots: Sequence[str], output_dir: str) -> Config:
        roots = _clean_roots(asset_roots)
        target = output_dir.strip()
        if not target:
            raise ValueError("输出目录是空的 —— 填一个目录，相对仓库根或绝对路径都行。")
        return self._apply({"paths": {"asset_roots": list(roots), "output_dir": target}})

    def set_theme(self, theme: str) -> Config:
        known = [value for value, _ in THEME_LABELS]
        if theme not in known:
            raise ValueError(f"不认识这个主题：{theme} —— 能选的只有 {'、'.join(known)}。")
        return self._apply({"ui": {"theme": theme}})

    def rescan_assets(self) -> str:
        """照当下的配置重扫一遍素材索引，回一句提醒 —— 没出错就是空串。

        素材库那一页读的是索引，不重扫就还认得旧目录，所以路径那一次保存连它一起做。
        四百来张扫一遍几秒，不值得为它单开一套后台重扫。

        重扫失败不算保存失败：配置已经落好了，回一句人话让人自己再跑一次。
        """
        from .assets import build_index, save_index

        try:
            index = build_index(self._config.paths.asset_roots)
            save_index(index, self._config.paths.asset_index)
        except Exception as exc:
            return (
                f"路径记下了，但重扫索引失败（{exc}）"
                " —— 手动跑一次：python -m lab assets scan"
            )
        return ""

    def _apply(self, updates: Mapping[str, Mapping[str, Any]]) -> Config:
        """写回 → 重读。中途读不回来就还原，再把错抛出去。"""
        path = self.local_path()
        before = path.read_text(encoding="utf-8") if path.is_file() else None
        write_local(path, updates)
        try:
            self._config = load_config(self._config.repo_root)
        except Exception:
            _restore(path, before)
            raise
        return self._config


def _clean_roots(values: Sequence[str]) -> tuple[str, ...]:
    """去掉空行与写重了的那几行，顺序照给的原样。"""
    seen: dict[str, None] = {}
    for value in values:
        root = value.strip()
        if root:
            seen.setdefault(root, None)
    return tuple(seen)


def _restore(path: Path, before: str | None) -> None:
    if before is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(before, encoding="utf-8")


def write_local(path: Path, updates: Mapping[str, Mapping[str, Any]]) -> None:
    r"""把几段几键写进 local.toml，别处原样留着。

    `updates` 的形状是 `{段名: {键: 值}}`；值只认字符串、整数与字符串列表。
    """
    target = Path(path)
    text = target.read_text(encoding="utf-8") if target.is_file() else ""
    for section, pairs in updates.items():
        for key, value in pairs.items():
            text = _rewrite(text, section, key, value)
    _write_atomically(target, text)


def _rewrite(text: str, section: str, key: str, value: Any) -> str:
    """原地改一处：段里有的换掉，段里没有的加到段尾，段都没有的补一段。"""
    lines = text.splitlines()
    start, end = _section_bounds(lines, section)
    rendered = _render(key, value)
    if start >= 0:
        found = _key_line(lines, start, end, key)
        if found >= 0:
            lines = lines[:found] + rendered + lines[_value_end(lines, found) + 1 :]
        else:
            at = _section_tail(lines, start, end)
            lines = lines[:at] + rendered + lines[at:]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines = lines + [f"[{section}]"] + rendered
    return "\n".join(lines) + "\n"


def _section_bounds(lines: list[str], name: str) -> tuple[int, int]:
    """这一段的内容占哪几行（不含段头那行）。没有这一段就回 (-1, -1)。"""
    for index, line in enumerate(lines):
        found = _SECTION.match(line)
        if found and found.group(1).strip() == name:
            for below in range(index + 1, len(lines)):
                if _SECTION.match(lines[below]):
                    return index + 1, below
            return index + 1, len(lines)
    return -1, -1


def _key_line(lines: list[str], start: int, end: int, key: str) -> int:
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    for index in range(start, end):
        if pattern.match(lines[index]):
            return index
    return -1


def _section_tail(lines: list[str], start: int, end: int) -> int:
    """段尾该插在哪一行：跳过尾部的空行，插在最后一行内容后面。"""
    last = end - 1
    while last >= start and not lines[last].strip():
        last -= 1
    return last + 1


def _value_end(lines: list[str], line: int) -> int:
    """这个键的值写到第几行 —— 多行数组要整段吃掉，只换第一行会留下半个数组。"""
    head = lines[line].split("=", 1)[1] if "=" in lines[line] else ""
    depth = _bracket_depth(head)
    while depth > 0 and line + 1 < len(lines):
        line += 1
        depth += _bracket_depth(lines[line])
    return line


def _bracket_depth(text: str) -> int:
    """数方括号的深度（引号里的不算，`#` 后面是注释）。"""
    depth = 0
    quote = ""
    for char in _code_part(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
    return depth


def _code_part(line: str) -> str:
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:index]
    return line


def _render(key: str, value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return [f"{key} = []"]
        return [f"{key} = [", *(f"  {_literal(item)}," for item in items), "]"]
    return [f"{key} = {_literal(value)}"]


def _literal(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"这个值写不进 TOML：{value!r}")


def _write_atomically(path: Path, text: str) -> None:
    """写临时文件再换名 —— 中途出事也不会留下半份配置。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    temp_path = Path(temp)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
