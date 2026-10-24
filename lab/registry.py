"""两本账：当天的全量记录与仓库里的配方库（票 #11，ADR 0003）。

分成两本的理由只有一个 —— **值不值得进版本控制**：

- 全量记录（`output/<日期>/registry.local.json`）：当天每一次生成的每一张，
  含废图。成品图几百 MB 且随时能重生成，跟着图一起躺在 `output/` 里（那个目录
  整个被 .gitignore 掉），只在本机、只活当天。
- 配方库（`data/registry.json`）：定稿的那几十条。成品丢了能重出，"什么参数能出
  什么图"丢了就得从头试，所以这一本进 git。

两本账用同一个条目形状（docs/design.md §6.2）—— 差别只在收哪些、"保留"这个动作
发生在什么时候。`keep` 就是把一条从左边搬到右边。

纯标准库读写 JSON，图片宽高由调用方量好传进来：这里不该知道 Pillow 的存在。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "Entry",
    "EntrySource",
    "LIBRARY_VERSION",
    "RECORD_NAME",
    "library_path",
    "load_entries",
    "now_iso",
    "record_path",
    "write_entry",
]

#: 全量记录的文件名。与 `assets/index.local.json` 一个路数：`.local` 是本机文件。
RECORD_NAME = "registry.local.json"

#: 条目形状的版本。将来加字段要能认出旧文件。
LIBRARY_VERSION = 1

#: 配方库顶上那句话 —— 写给将来打开这个文件、想知道"这堆东西是什么"的人。
LIBRARY_COMMENT = (
    "配方库：只记定稿条目（去背后 / 加字后 / 手工标记保留的）。"
    "全量记录在 output/<日期>/registry.local.json，不进 git。字段说明见 docs/design.md §6.2。"
)

RECORD_COMMENT = (
    "全量记录：当天每一次生成的每一张，含废图。只在本机、只活当天，不进版本控制。"
    "挑中的定稿用 python -m lab keep 写进 data/registry.json。"
)


@dataclass(frozen=True)
class EntrySource:
    """这条配方的来源图 —— 有它才谈得上重绘强度。纯文生图时整块是 null。"""

    type: str
    image: str
    denoise: float

    def as_json(self) -> dict[str, Any]:
        return {"type": self.type, "image": self.image, "denoise": self.denoise}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> EntrySource:
        return cls(
            type=str(data.get("type") or ""),
            image=str(data.get("image") or ""),
            denoise=float(data.get("denoise") or 0.0),
        )


#: 少了一个就没法把这条对回一张图的那几个键。其余字段缺了按默认值走 ——
#: imported 那种来历不明的图，seed 是 0、model 是空串，本来就是合法的一条。
_REQUIRED_KEYS = ("id", "createdAt", "kind", "file", "width", "height")


@dataclass(frozen=True)
class Entry:
    """一条配方：什么参数、什么来源、出了哪张图。

    字段名照 docs/design.md §6.2 的对外形状（camelCase、预留三个 null），
    在代码里用下划线是 Python 的习惯，落到 JSON 时才是那份契约。
    `comfyui_version` 是本票加的：同种子同参数在不同引擎版本下不保证出同一张，
    版本不记下来，复现时就没法判断差异是"参数变了"还是"换引擎了"。

    `width`/`height` 是**成品**的像素（要给站点用，必须描述真实文件）；
    `requested_width`/`requested_height` 是**请求生成时**填进「尺寸」节点的那个值。两者
    正常情况下相等，但图被引擎缩过、或者手工裁过之后就分家了 —— 只记成品那一对，
    重跑时会照着错的大小喂引擎，所以两个都得留。0 表示不知道（外来的图）。
    """

    id: str
    created_at: str
    kind: str
    file: str
    width: int
    height: int
    workflow: str
    model: str
    prompt: str
    negative: str
    seed: int
    steps: int
    cfg: float
    comfyui_version: str
    requested_width: int = 0
    requested_height: int = 0
    animated: bool = False
    source: EntrySource | None = None
    caption: str | None = None  # glossary-ok: 字段名照 docs/design.md §6.2 的对外形状

    @property
    def key(self) -> tuple[str, str, str]:
        """一张图在账里的唯一标识：号 + 阶段 + 哪一天。

        带上日期那一截是必须的：号是"同一天内自增"的，两本账却都要长期累积 ——
        配方库跨日攒的东西里，10-23 的 fish-001 与 10-24 的 fish-001 是两次无关
        的生成，只按（号，阶段）去重会把前一条静默盖掉。
        """
        return (self.id, self.kind, self.created_at[:10])

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "createdAt": self.created_at,
            "kind": self.kind,
            "file": self.file,
            "width": self.width,
            "height": self.height,
            "animated": self.animated,
            "workflow": self.workflow,
            "model": self.model,
            "prompt": self.prompt,
            "negative": self.negative,
            "seed": self.seed,
            "steps": self.steps,
            "cfg": self.cfg,
            "comfyuiVersion": self.comfyui_version,
            "requestedWidth": self.requested_width,
            "requestedHeight": self.requested_height,
            "source": self.source.as_json() if self.source is not None else None,
            "caption": self.caption,  # glossary-ok: 字段名照 docs/design.md §6.2
            # 三个站点资源路径：本工作台不做站，恒为 null，留作语义占位（§6.2）
            "src": None,
            "preview": None,
            "large": None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Entry:
        # 值为 null 和缺键是同一件事：`str(None)` 会洗成字面 `'None'`，
        # 那条记录从此答不出"这张图怎么来的"，而它还会被原样写回账里。
        missing = [key for key in _REQUIRED_KEYS if data.get(key) is None]
        if missing:
            # 缺键和"JSON 坏了"是同一类事：这本账读不全，读账的人得知道缺的是哪个键。
            # 抛 ValueError 而不是漏出 KeyError —— 调用方只接这一种，漏出去就是 traceback。
            ident = data.get("id") or "（没有 id）"
            raise ValueError(f"条目 {ident} 缺了 {'、'.join(missing)}")
        source = data.get("source")
        return cls(
            id=str(data["id"]),
            created_at=str(data["createdAt"]),
            kind=str(data["kind"]),
            file=str(data["file"]),
            width=int(data["width"]),
            height=int(data["height"]),
            workflow=str(data.get("workflow") or ""),
            model=str(data.get("model") or ""),
            prompt=str(data.get("prompt") or ""),
            negative=str(data.get("negative") or ""),
            seed=int(data.get("seed") or 0),
            steps=int(data.get("steps") or 0),
            cfg=float(data.get("cfg") or 0.0),
            comfyui_version=str(data.get("comfyuiVersion") or "未知"),
            requested_width=int(data.get("requestedWidth") or 0),
            requested_height=int(data.get("requestedHeight") or 0),
            animated=bool(data.get("animated") or False),
            source=EntrySource.from_json(source) if isinstance(source, dict) else None,
            # glossary-ok: 字段名照 docs/design.md §6.2 的对外形状
            caption=data.get("caption") or None,  # glossary-ok: 同上，键名不能改
        )


def record_path(output_dir: Path, today: date | None = None) -> Path:
    """全量记录落在当天那个目录里 —— 跟当天的图放一起，看的时候不用两头找。"""
    day = today or date.today()
    return Path(output_dir) / day.isoformat() / RECORD_NAME


def library_path(repo_root: Path) -> Path:
    """配方库：仓库里 `data/registry.json`，这是唯一要进 git 的那本。"""
    return Path(repo_root) / "data" / "registry.json"


def load_entries(path: Path) -> list[Entry]:
    """读一本账。文件不在就是空的（第一次出片时还没这个文件）。

    文件坏了抛 ValueError 并带上路径 —— 静默当成空的会把整整一天记的东西吞掉。
    """
    path = Path(path)
    if not path.is_file():
        return []
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} 不是合法 JSON：{exc} —— 修好它或挪走再重来。") from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"{path} 里该是一个对象，实际是 {type(data).__name__} —— 修好它或挪走再重来。"
        )
    if "items" not in data:
        # 没有 items 键的账是合法的空账（第一次写之前就是这个形状）。
        return []
    items = data["items"]
    if not isinstance(items, list):
        # `or []` 那种写法会把 ""/0/false/null 静默当成空账，而 write_entry 是
        # 整本读-改-写：读成空账等于把这一天记的条目全抹掉。
        raise ValueError(
            f"{path} 的 items 该是一串条目，实际是 {type(items).__name__}"
            " —— 修好它或挪走再重来。"
        )
    try:
        return [Entry.from_json(item) for item in items]
    except (ValueError, TypeError, AttributeError) as exc:
        # 键缺了、值是 null、条目根本不是对象 —— 对读账的人来说是同一件事：
        # 这本账读不全。三种都收进来，别让哪一种漏成 traceback。
        raise ValueError(f"{path} 里有一条读不出来：{exc} —— 修好它或挪走再重来。") from exc


def now_iso() -> str:
    """条目时间戳的形状：本地时区、秒级、带偏移（`2026-10-24T02:05:43+08:00`）。

    带偏移而不是 UTC：这个工作台只在一个时区里用，"今天出了几张"是本地日期的事。
    """
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def write_entry(path: Path, entry: Entry, *, library: bool = False) -> None:
    """把一条写进账本；同一个 (id, kind) 已有就覆盖，没有就追加。

    每次都读-改-写整本而不是追加一行：条目是 JSON 数组里的对象，追加要先改上一行
    的收尾，改一次比整本重写更容易写坏。这本账一天几十条，代价可以忽略。

    `library=True` 时用配方库的那句抬头 —— 两本账的形状一样，只有这句话不同。
    """
    path = Path(path)
    existing = load_entries(path)
    entries = [one for one in existing if one.key != entry.key]
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": LIBRARY_COMMENT if library else RECORD_COMMENT,
        "version": LIBRARY_VERSION,
        "updatedAt": now_iso(),
        "items": [one.as_json() for one in entries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
