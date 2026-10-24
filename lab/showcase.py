"""成品库那一页要的东西：配方摊成卡片、按日期分组、按关键词筛。

这一层只做"摊开与筛"，不碰页面也不碰 HTTP —— 缩略图怎么缩在
`lab.showcase.thumb_bytes`，页面怎么摆是 `lab.web` 的事，配方从哪来是
`lab.keeping.gallery` 的事。分成三处的理由与 naming / paths 一样：同一个问题
只回答一次，"哪张是旧的那张""它属于哪一天"才不会在两处各算一遍。

卡片认的是配方条目的身份（号 + 阶段 + 日期那一截），不是它在盘上的路径：
配方库里的 `file` 记的是仓库相对路径，图被搬走、被清空之后路径就不在了，
但那一条配方还在 —— 成品库该把"配方还在、图不在了"如实摊出来，而不是干脆
装看不见。取缩略图时才去碰真文件，碰不到就报那一句。
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import Config
from .keeping import IMPORTED_KIND, gallery
from .recording import resolve_recorded
from .registry import Entry, record_path

__all__ = [
    "THUMB_WIDTH",
    "Card",
    "Group",
    "cards",
    "find_card",
    "groups",
    "picture_path",
    "record_of",
    "search",
    "thumb_bytes",
]

#: 卡片那一档：240 CSS 像素宽，按 2 倍屏算。
THUMB_WIDTH = 480


def thumb_bytes(path: Path) -> bytes:
    """把这张成品缩到卡片那一档，回 PNG 字节。

    只缩不放大 —— 比卡片还小的图（拷进来的外来图）保持原样，放大只是把模糊
    摊开。带透明区的贴纸照着缩，不垫白底：去背后那张的透明就是它的成品形态。
    """
    from PIL import Image

    try:
        with Image.open(path) as opened:
            image = opened.copy()
    except (OSError, ValueError) as exc:
        raise ValueError(f"这张缩不出来：{Path(path).name} —— {exc}") from None

    if image.width > THUMB_WIDTH:
        height = max(1, round(image.height * THUMB_WIDTH / image.width))
        image = image.resize((THUMB_WIDTH, height), Image.Resampling.LANCZOS)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass(frozen=True)
class Card:
    """配方库里的那一条，摊成页面上的一张卡片。"""

    entry: Entry

    @property
    def day(self) -> str:
        """它属于哪一天：账里那条的目的日期那一截（身份的第 3 段，别切第二遍）。"""
        return self.entry.key[2]

    @property
    def thumb(self) -> str:
        """缩略图的路由尾巴（页面自己接上接口前缀），也是这一条的稳定身份。"""
        return f"{self.day}/{self.entry.id}/{self.entry.kind}.png"


@dataclass(frozen=True)
class Group:
    """同一天留下的那几条，最近的一天排最前。"""

    day: str
    cards: tuple[Card, ...]


def cards(config: Config) -> list[Card]:
    """配方库里每一条都摊成一张卡片，最近留的最前（顺序沿用 keeping.gallery）。"""
    return [Card(entry) for entry in gallery(config)]


def search(items: list[Card], query: str) -> list[Card]:
    """按关键词筛：台词、模型、种子、号，四样都算命中，不挑大小写。

    空白当没搜 —— 成品库默认摊全部，不是默认摊空的。
    """
    needle = query.strip().lower()
    if not needle:
        return list(items)
    return [item for item in items if needle in _haystack(item.entry)]


def _haystack(entry: Entry) -> str:
    """一条配方能被搜到的全部字面：四样拼一次，别在筛的时候各拼各的。"""
    return " ".join(
        (entry.prompt, entry.model, str(entry.seed), entry.id)
    ).lower()


def groups(items: list[Card]) -> list[Group]:
    """按日期分组，最近的一天在前；同一天里保持传进来的相对顺序。"""
    buckets: dict[str, list[Card]] = {}
    for item in items:
        buckets.setdefault(item.day, []).append(item)
    return [
        Group(day=day, cards=tuple(buckets[day]))
        for day in sorted(buckets, reverse=True)
    ]


def find_card(config: Config, day: str, ident: str, kind: str) -> Card:
    """按（日期, 号, 阶段）找回账里那一条 —— 缩略图与详情都靠它定位。"""
    for item in cards(config):
        entry = item.entry
        if (item.day, entry.id, entry.kind) == (day, ident, kind):
            return item
    raise ValueError(
        f"成品库里没有 {day} 的 {ident}.{kind} —— "
        "缩略图是从配方库现取现缩的，账里没有这一条就没有这张图。"
    )


def picture_path(config: Config, card: Card) -> Path | None:
    """这条配方的图现在在哪：账里记的路径换算回本机，不在原处就是 None。

    回 None 而不是抛异常 —— 成品库里图被清走是常事，一页要问几十张，一张不在
    不该让整页问不下去。要一张图的那两处（缩略图端点）自己把 None 翻成 404。
    """
    path = resolve_recorded(config, card.entry.file)
    return path if path.is_file() else None


def record_of(config: Config, card: Card) -> dict[str, Any]:
    """当天全量记录里那一条的完整字段（页面的「查看登记项」）。

    全量记录按天一本、与配方库不是同一本：配方库记的是"我留了什么"，全量记录
    记的是"当天出了什么"。所以这里要按日期回那本里找，不在就照实说 —— 图进来
    之后账被清走过，这条就只剩配方库里那一半了。
    """
    from datetime import date as _date

    from .registry import load_entries

    day = _date.fromisoformat(card.day)
    for entry in load_entries(record_path(config.paths.output_dir, day)):
        if entry.key == card.entry.key:
            return entry.as_json()
    if card.entry.kind == IMPORTED_KIND:
        # 外来的图只写进配方库，当天那本从头到尾没写过它 —— 两件事说法不一样。
        raise ValueError(
            f"{card.day} 的全量记录里本来就没有 {card.entry.id} 这一条 —— "
            "外来的图只记进配方库，生成那几栏从来是空的。"
        )
    raise ValueError(
        f"{card.day} 的全量记录里没有 {card.entry.id}.{card.entry.kind} 这一条 —— "
        "那条记录可能已经随当天目录被清掉了，配方库里这一半还在。"
    )
