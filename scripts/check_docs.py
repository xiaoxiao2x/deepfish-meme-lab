#!/usr/bin/env python3
"""文档与代码的对账：别让两处说法各自漂。

两类，都在本会话里踩过：

1. **标题集合**。`docs/design.md` §6.3 用两句话写死了模板必须有哪几个 `_meta.title`
   （生成一套、后处理一套），`lab.workflows` 里是同一件事的另一份写法。两处不一致时，
   要么注入静默找不到节点，要么文档在骗人。这里逐字读那两行，跟常量比 —— 双向比，
   多一个少一个都报。
2. **用词**。`GLOSSARY.md` 给每个领域词定了「别叫它什么」。源码、模板、默认配置里的
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

from lab.workflows import (  # noqa: E402
    REQUIRED_TITLES,
    STICKER_TITLES,
    TITLE_DENOISE,
    TITLE_INIT_IMAGE,
    TITLE_OUTPUT,
    TITLE_SAMPLER,
)

DESIGN = REPO_ROOT / "docs" / "design.md"

#: 那两句话的开头。锚点必须唯一，否则检查等于没做 —— 文档一改，这里要跟着改。
GENERATION_ANCHOR = "约定标题集合："
STICKER_ANCHOR = "后处理模板另有一套短标题"

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
        raise SystemExit(f"docs/design.md 里「{anchor}」命中 {len(hits)} 行 —— 锚点得唯一，检查才有意义")
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
    compare_titles("生成模板", documented_titles(find_anchor(GENERATION_ANCHOR)), generation, problems)
    compare_titles("后处理模板", documented_titles(find_anchor(STICKER_ANCHOR)), set(STICKER_TITLES), problems)


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
    scanned = check_words(problems)

    if problems:
        print("文档与代码对不上：")
        for problem in problems:
            print(f"  · {problem}")
        if any("GLOSSARY" in problem for problem in problems):
            print("（确实该这么写：在那一行加 glossary-ok，把理由写在旁边）")
        return 1

    print(f"文档与代码对得上：标题集合两处一致，{scanned} 个文件没碰 GLOSSARY 不收的词。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
