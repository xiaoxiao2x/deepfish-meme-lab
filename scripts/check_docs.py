#!/usr/bin/env python3
"""文档与代码的对账：别让两处说法各自漂。

四类，都在本会话里踩过：

1. **标题集合**。`docs/design.md` §6.3 用两句话写死了模板必须有哪几个 `_meta.title`
   （生成一套、后处理一套），`lab.workflows` 里是同一件事的另一份写法。两处不一致时，
   要么注入静默找不到节点，要么文档在骗人。这里逐字读那两行，跟常量比 —— 双向比，
   多一个少一个都报。
2. **配方条目的键集**。§6.2 那个 jsonc 代码块是对外契约，`lab.registry.Entry.as_json`
   是它的实现。加一个字段要同时改四处（dataclass、as_json、from_json、§6.2），
   靠人记早晚会漂 —— 这里逐键比，并且实跑一次 as_json → from_json → as_json，
   确认读回来再写出去还是同一份。
3. **模块地图**。§4 的架构树是「lab/ 里有哪几层」的地图，紧跟其后的「分层职责」是
   讲解。两个方向都要守：树上列了 `lab/` 里没有的文件就报（改名忘改树），
   讲解里用反引号提到了某个模块、树上却没有也报（讲了却不在图上 —— 本次
   `recording.py`、`keeping.py` 就是这么漏的）。
4. **用词**。`GLOSSARY.md` 给每个领域词定了「别叫它什么」。源码、模板、默认配置里的
   名字与注释属于工作台自己的话，用错词就改；`docs/` 不扫 —— 文档得能引用禁用词来
   解释词义（说什么「别叫它出图」，总得写出「出图」两个字）。

行内出现 `glossary-ok` 的整行放过：豁免要写在明面上，别让人猜为什么这里不算。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lab.registry import Entry, EntrySource  # noqa: E402
from lab.workflows import (  # noqa: E402
    REQUIRED_TITLES,
    STICKER_TITLES,
    TITLE_DENOISE,
    TITLE_INIT_IMAGE,
    TITLE_OUTPUT,
    TITLE_SAMPLER,
)

DESIGN = REPO_ROOT / "docs" / "design.md"
LAB = REPO_ROOT / "lab"

#: 那两句话的开头。锚点必须唯一，否则检查等于没做 —— 文档一改，这里要跟着改。
GENERATION_ANCHOR = "约定标题集合："
STICKER_ANCHOR = "后处理模板另有一套短标题"
SCHEMA_ANCHOR = "### 6.2 配方条目 schema"
ARCH_ANCHOR = "## 4. 架构"
DUTY_ANCHOR = "**分层职责**："

#: 反引号里只认纯中文词：那两行还夹着 `lab.workflows` 这类代码标识符。
CHINESE_WORD = re.compile(r"^[\u4e00-\u9fff]{1,6}$")
BACKTICKED = re.compile(r"`([^`]+)`")

#: GLOSSARY 里定了「别叫它什么」的词，只留**出现在源码/模板里就是错的**那批。
#: 排除的三个都是有意的：「加工厂」是项目名本身；「表情包」是产品定位（GLOSSARY 只是说
#: 它不是「贴纸」的同义词）；「源图」「原图」在「待处理的那张图」这个语境里还没有正式叫法
#: —— 那是词表缺口，属判断题，不该做成门禁。
BANNED = (
    "产线",
    "模型服务",
    "推理服务",
    "画图模块",
    "出图",
    "渲染",
    "导出",
    "产物",
    "输出物",
    "透明图",
    "抠图",
    "去底",
    "背景移除",
    "文案",
    "字幕",
    "caption",
    "人设",
    "画风",
    "参考图",
    "降噪强度",
    "相似度",
    "原料",
    "图片库",
    "原料池",
    "图库",
    "资源池",
    "排重",
    "清洗",
    "重复项",
    "相似图",
    "登记册",
)

#: 中文词直接找；英文词要求前后不是字母或数字（下划线不算边界）。这条比 \b 严：
#: \b 把下划线当边界，于是 `_run_caption`、`caption_parser` 这类标识符整块逃掉 ——
#: 词表名义上管着、实际一路绿。反过来 `captioned`（对外后缀，词表允许）后面跟着
#: 字母，照样不命中 —— 严的是标识符，不是派生词。
BANNED_PATTERN = re.compile(
    "|".join(
        rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])" if w.isascii() else w
        for w in BANNED
    )
)

#: §6.2 代码块里的一行键：缩进两格的那层才是对外契约的顶层键。
SCHEMA_KEY = re.compile(r'"([A-Za-z]+)":')

#: §4 架构树里的一行模块：`│  naming.py    成品落点与编号 │` 的中间那截。
TREE_MODULE = re.compile(r"^\s*│\s+(\w+)\.py\b")

#: 分层职责里提到的模块：用反引号包着的 `xxx.py`。
DUTY_MODULE = re.compile(r"`(\w+)\.py`")

#: lab/ 里**故意不上架构树**的文件，每个都得说出理由 —— 这条检查的价值不在"树要穷举
#: 文件名"，而在"新增一个模块时必须做一次决定"：要么图上加一行，要么进这张说明。
#: 静默漂移就是这么进来的（`recording.py`、`keeping.py` 一度既不在图上、也没人问过）。
#: 键是模块名（不带 .py），跟 `Path.stem` 一致。
OFF_TREE = {
    "__init__": "包的入口，不是一层",
    "__main__": "python -m lab 的转发，不是一层",
    "config": "配置读取，被每一层用，不属于任何一层",
    "paths": "素材后缀表与排除规则，零依赖的小工具",
    "contract": "模板与引擎的节点清单对账，医生用它，不是出片链路的一层",
    "doctor": "环境体检，只读探测，不在出片链路上",
    "pipeline": "串起出片：图上那三层（引擎 / 命名 / 记账）的粘合，本身不是一层",
    "stub": "假引擎，测试与无显卡调试用，不是工作台的一层",
}

#: 扫哪些：工作台自己的话。不扫 docs/（见 docstring），不扫 config/local.toml（本机文件）。
SCANNED_GLOBS = (
    "lab/**/*.py",
    "tests/**/*.py",
    "scripts/**/*.py",
    "scripts/**/*.sh",
    "workflows/*.json",
    "config/default.toml",
)

#: 这个脚本自己写着整张词表，扫自己只会得到一堆自己。
EXEMPT_FILES = {Path(__file__).resolve()}


def documented_titles(line: str) -> set[str]:
    """从那句话里挑出标题：反引号包着、且整个是中文短词的那些。"""
    return {word for word in BACKTICKED.findall(line) if CHINESE_WORD.match(word)}


def find_anchor(anchor: str) -> str:
    hits = [line for line in DESIGN.read_text(encoding="utf-8").splitlines() if anchor in line]
    if len(hits) != 1:
        raise SystemExit(
            f"docs/design.md 里「{anchor}」命中 {len(hits)} 行 —— 锚点得唯一，检查才有意义"
        )
    return hits[0]


def compare_titles(what: str, documented: set[str], code: set[str], problems: list[str]) -> None:
    missing = sorted(code - documented)
    extra = sorted(documented - code)
    if missing:
        problems.append(
            f"docs/design.md 的{what}标题集合少了 {'、'.join(missing)}（lab.workflows 里有）"
        )
    if extra:
        problems.append(
            f"docs/design.md 的{what}标题集合多了 {'、'.join(extra)}（lab.workflows 里没有）"
        )


def check_titles(problems: list[str]) -> None:
    generation = {*REQUIRED_TITLES, TITLE_SAMPLER, TITLE_INIT_IMAGE, TITLE_DENOISE, TITLE_OUTPUT}
    compare_titles(
        "生成模板", documented_titles(find_anchor(GENERATION_ANCHOR)), generation, problems
    )
    compare_titles(
        "后处理模板", documented_titles(find_anchor(STICKER_ANCHOR)), set(STICKER_TITLES), problems
    )


def design_block(anchor: str) -> list[str]:
    """取锚点之后第一个代码块里的行。锚点仍要唯一（find_anchor 负责喊）。"""
    find_anchor(anchor)
    lines = DESIGN.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if anchor in line)
    opened = next(index for index in range(start, len(lines)) if lines[index].startswith("```"))
    closed = next(
        index for index in range(opened + 1, len(lines)) if lines[index].startswith("```")
    )
    return lines[opened + 1 : closed]


def documented_keys() -> set[str]:
    """§6.2 代码块里的顶层键。缩进两格的是契约层，再深的属于子对象。"""
    keys: set[str] = set()
    for line in design_block(SCHEMA_ANCHOR):
        if line.startswith("  ") and not line.startswith("   "):
            keys.update(SCHEMA_KEY.findall(line))
    return keys


def sample_entry() -> Entry:
    """填满每个字段的一条，用来问 as_json「你吐出哪些键」。"""
    return Entry(
        id="fish-001",
        created_at="2026-10-22T00:31:05+08:00",
        kind="orig",
        file="output/2026-10-22/fish-001.orig.png",
        width=240,
        height=240,
        workflow="workflows/fish-txt2img.json",
        model="flux-2-klein-base-4b-fp8.safetensors",
        prompt="chibi blue whale girl",
        negative="",
        seed=123456,
        steps=50,
        cfg=3.5,
        comfyui_version="0.3.61",
        requested_width=1024,
        requested_height=1024,
        source=EntrySource(type="img2img", image="~/pictures/base.webp", denoise=0.55),
        caption="你好呀",  # glossary-ok: 字段名照 docs/design.md §6.2 的对外形状
    )


def compare_keys(problems: list[str]) -> None:
    documented = documented_keys()
    written = set(sample_entry().as_json())
    missing = sorted(written - documented)
    extra = sorted(documented - written)
    if missing:
        problems.append(
            f"docs/design.md §6.2 的条目 schema 少了 {'、'.join(missing)}（Entry.as_json 里有）"
        )
    if extra:
        problems.append(
            f"docs/design.md §6.2 的条目 schema 多了 {'、'.join(extra)}（Entry.as_json 里没有）"
        )


def check_schema(problems: list[str]) -> None:
    """§6.2 的键集与 as_json 双向比，再确认读回来写出去还是同一份。

    键集对得上不代表读得回来：`from_json` 漏读一个键，写第二遍就把它丢了 ——
    而那正是配方库最不该发生的事。
    """
    compare_keys(problems)
    entry = sample_entry()
    first = entry.as_json()
    again = Entry.from_json(first).as_json()
    if again != first:
        drifted = sorted(key for key in first if first.get(key) != again.get(key))
        problems.append(
            f"Entry 读回来再写出去跟原来不一样：{'、'.join(drifted) or '（键集合变了）'}"
        )


def tree_modules() -> set[str]:
    return {
        match.group(1)
        for line in design_block(ARCH_ANCHOR)
        if (match := TREE_MODULE.match(line))
    }


def lab_modules() -> set[str]:
    return {path.stem for path in sorted(LAB.glob("*.py"))}


def check_arch(problems: list[str]) -> None:
    """§4 的架构树与 lab/ 实际文件双向比；讲了却不在图上的也要报。

    树列了 lab/ 里没有的文件：改名或删模块之后忘了改图，读文档的人会去找一个不存在的文件。
    lab/ 里有树上没有的文件：新加的模块没人做决定（画上图还是写进 OFF_TREE 说明为什么不上）。
    分层职责里提到却不在图上：这一次真踩到过 —— 两段文字都讲了，图上却没有它们，
    于是"架构里到底有哪几层"有两个答案。
    """
    tree = tree_modules()
    actual = lab_modules()

    ghost = sorted(tree - actual)
    if ghost:
        problems.append(f"docs/design.md §4 的架构树列了 lab/ 里没有的文件：{'、'.join(ghost)}")

    unexplained = sorted(actual - tree - set(OFF_TREE))
    if unexplained:
        problems.append(
            f"lab/ 里这些文件不在架构树上：{'、'.join(unexplained)}"
            "（要么在 §4 树上加一行，要么写进 scripts/check_docs.py 的 OFF_TREE 说明为什么不上）"
        )

    stale = sorted(set(OFF_TREE) - actual)
    if stale:
        problems.append(
            f"scripts/check_docs.py 的 OFF_TREE 里这些文件已经不在 lab/ 了：{'、'.join(stale)}"
        )

    lines = DESIGN.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if DUTY_ANCHOR in line)
    end = next(
        index for index in range(start + 1, len(lines)) if line_moves_on(lines[index])
    )
    spoken = {
        name
        for line in lines[start:end]
        for name in DUTY_MODULE.findall(line)
        if name not in OFF_TREE
    }
    off_map = sorted(spoken - tree)
    if off_map:
        problems.append(
            f"docs/design.md §4 的「分层职责」讲了 {('、'.join(off_map))}，但架构树上没有它们"
        )


def line_moves_on(line: str) -> bool:
    """分层职责那一段到「明确不做的架构决定」为止。"""
    return line.startswith("**明确不做的架构决定**")


def check_words(problems: list[str]) -> int:
    scanned = 0
    for pattern in SCANNED_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.resolve() in EXEMPT_FILES:
                continue
            scanned += 1
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "glossary-ok" in line:
                    continue
                found = list(dict.fromkeys(BANNED_PATTERN.findall(line)))
                if found:
                    where = path.relative_to(REPO_ROOT)
                    problems.append(f"{where}:{number} 用了 GLOSSARY 不收的词：{'、'.join(found)}")
    return scanned


def main() -> int:
    problems: list[str] = []
    check_titles(problems)
    check_schema(problems)
    check_arch(problems)
    scanned = check_words(problems)

    if problems:
        print("文档与代码对不上：")
        for problem in problems:
            print(f"  · {problem}")
        if any("GLOSSARY" in problem for problem in problems):
            print("（确实该这么写：在那一行加 glossary-ok，把理由写在旁边）")
        return 1

    print(
        f"文档与代码对得上：标题集合两处一致，§6.2 的 "
        f"{len(documented_keys())} 个键与 Entry 对得上，架构树与 lab/ 的 "
        f"{len(lab_modules())} 个文件对得上，{scanned} 个文件没碰 GLOSSARY 不收的词。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
