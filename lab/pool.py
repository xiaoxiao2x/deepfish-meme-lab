"""素材库：把 `lab assets scan` 建的那本索引摆成一页挑得动的东西。

三条边界钉在这里，页面与路由都只照它做：

- **只读**：素材是引用，不是仓库的一部分（design.md §7.2）。这一层只读文件头
  算缩略图，绝不写、不改、不搬 —— 「我们没动它」这句话要能自己证明。
- **照索引认**：一张素材能不能显示，取决于索引里有没有它。路径本身不是通行证，
  否则这个页面就成了「你给个路径我就读」。
- **用没用过看账**：账里某条的 `source.image` 指着这张素材，它就算用过。配方库
  与当天的全量记录都算 —— 出过片没保留的，同样不该再算「没用过」。

「指着这张素材」有两种写法，都得认：素材原本就在 output 里时，账上记的是它的
相对路径；从素材池送进去当底图时，出片前会先落一份副本（引擎只读得懂 output 里
的图），账上记的是那份副本 —— 副本名由 copy_name 定，认副本就是认那截 sha1。
只认前一种，`?unused=1` 在真实链路上永远不过滤（手造账能把测试骗绿，真实流程
产生不了那个形状）。

瀑布流要的分栏、懒加载、筛选控件都在页面上；这里只回答「有哪些」「哪些算用过」。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import thumbs
from .assets import Asset, Index, StaleIndex, load_index
from .config import Config
from .recording import repo_relative
from .registry import RECORD_NAME, library_path, load_entries

__all__ = [
    "ANIMATED",
    "KIND_LABELS",
    "SCAN_HINT",
    "STILL",
    "THUMB_WIDTH",
    "Batch",
    "Tile",
    "batches",
    "copy_name",
    "find_asset",
    "find_tile",
    "open_index",
    "sift",
    "thumb_bytes",
    "tiles",
]

#: 瀑布流那一格缩到多宽：与成品库的卡片同一档（见 lab/thumbs.py）。
THUMB_WIDTH = thumbs.WIDTH

STILL = "still"
ANIMATED = "animated"

#: 筛选下拉里的两档：值进 URL，字给人看。
KIND_LABELS: tuple[tuple[str, str], ...] = ((STILL, "静图"), (ANIMATED, "动图"))

#: 索引不在或读不出来时都要说的那半句话 —— 两条路指向同一个动作。
SCAN_HINT = "先跑 `python -m lab assets scan`，扫一遍素材根目录"


# ---------- 数据 ----------


@dataclass(frozen=True)
class Tile:
    """瀑布流里的一格。`asset` 是索引里那一条，`used` 是账上翻出来的结论。"""

    asset: Asset
    used: bool = False

    @property
    def name(self) -> str:
        return self.asset.path.name

    @property
    def key(self) -> str:
        """这一格的地址。用内容哈希而不是路径：路径里有本机的目录结构。"""
        return self.asset.sha1

    @property
    def kind(self) -> str:
        return ANIMATED if self.asset.animated else STILL


@dataclass(frozen=True)
class Batch:
    """一个来源批次 —— 扫描时的一个根。`root` 是筛选值，`name` 是给人看的。"""

    root: Path
    count: int

    @property
    def name(self) -> str:
        return self.root.name

    @property
    def path(self) -> str:
        return self.root.as_posix()


def copy_name(asset: Asset) -> str:
    """这张素材落进 output 时叫什么。真出片用的就是这个名字，账上记的也是它。

    名字照 sha1 定死：同一张素材反复送进来只落一次，换个日期也不算新的一张。
    后处理层那套 fish-### 编号管不上它 —— 素材还没有号。
    """
    return f"asset-{asset.sha1[:12]}{asset.path.suffix.lower()}"


# ---------- 索引 ----------


def open_index(cfg: Config) -> tuple[Index | None, str]:
    """读索引。读不出来就回 (None, 一句话)，让页面说人话而不是白屏。

    两种失败在这里合成同一种形状：文件还没有（该去扫一遍）、文件有但不对
    （版本换了、写到一半断电、手工改错）。两者的下一步是同一个动作，所以
    不必让调用方分辨。
    """
    path = Path(cfg.paths.asset_index)
    if not path.is_file():
        return None, f"还没有索引：{path} —— {SCAN_HINT}。"
    try:
        return load_index(path), ""
    except (StaleIndex, ValueError, OSError) as exc:
        return None, f"索引读不出来（{exc}）—— 重跑 `python -m lab assets scan` 重建。"


def tiles(cfg: Config, index: Index) -> list[Tile]:
    """索引里的每一条都摆成一格。顺序照索引（按根、再按路径排过）。"""
    used = _used(cfg)
    return [Tile(asset=one, used=_was_used(cfg, one, used)) for one in index.assets]


def find_asset(index: Index, key: str) -> Asset:
    """按内容哈希从索引里取那一张，**不翻账**。

    瀑布流一次能发几百个缩略图请求，而「用过没有」要读完整本账；缩略图与
    大图只问「索引里有没有这一张」，所以这一条换算不碰账。要连「用过没有」
    一起拿的，走 find_tile。

    索引里没有就抛 —— 这一条同时是「不是任意文件读取入口」的落点。
    """
    wanted = (key or "").strip()
    for asset in index.assets:
        if asset.sha1 == wanted:
            return asset
    raise ValueError(
        f"索引里没有 {key} 这一张 —— 缩略图是照索引现缩的，索引不记的东西这里也不显示。"
    )


def find_tile(cfg: Config, index: Index, key: str) -> Tile:
    """按内容哈希找回那一格，连「用过没有」一并算上。"""
    asset = find_asset(index, key)
    return Tile(asset=asset, used=_was_used(cfg, asset, _used(cfg)))


def batches(items: Sequence[Tile]) -> list[Batch]:
    """来源批次的清单，按索引里出现的先后来 —— 同一批的图本来就连在一起。"""
    counts: dict[str, int] = {}
    roots: dict[str, Path] = {}
    for tile in items:
        key = tile.asset.root.as_posix()
        roots.setdefault(key, tile.asset.root)
        counts[key] = counts.get(key, 0) + 1
    return [Batch(root=roots[key], count=count) for key, count in counts.items()]


def sift(
    items: Sequence[Tile],
    *,
    kind: str | None = None,
    batch: str | None = None,
    unused: bool = False,
) -> list[Tile]:
    """三个筛选叠着来：静图/动图、来源批次、只看没用过的。"""
    kept = list(items)
    if kind:
        wanted = kind.strip().lower()
        kept = [tile for tile in kept if tile.kind == wanted]
    if batch:
        wanted_root = Path(batch).as_posix()
        kept = [tile for tile in kept if tile.asset.root.as_posix() == wanted_root]
    if unused:
        kept = [tile for tile in kept if not tile.used]
    return kept


# ---------- 缩略图 ----------


def thumb_bytes(path: Path) -> bytes:
    """现缩一张。缩法只有一份（lab/thumbs.py）：只缩不放大。

    动图取第一帧：瀑布流里那一格是封面，不是播放器。
    """
    return thumbs.to_png(path)


# ---------- 账 ----------


@dataclass(frozen=True)
class _Used:
    """账上写着的东西，折成两种可比对的形状。

    `paths` 是原样的写法（素材本来就在 output 里时，账上记的就是它）；
    `copies` 是文件名，用来认那些从素材池送进去的副本。
    """

    paths: frozenset[str]
    copies: frozenset[str]


def _ledgers(cfg: Config) -> list[Path]:
    """要翻的账：配方库一本，加上 output 下每个日期一本全量记录。"""
    found: list[Path] = []
    library = library_path(cfg.repo_root)
    if library.is_file():
        found.append(library)
    output = Path(cfg.paths.output_dir)
    if output.is_dir():
        found.extend(sorted(output.glob(f"*/{RECORD_NAME}")))
    return found


def _used(cfg: Config) -> _Used:
    """账上写过的来路，两种形状都收下。

    记的是 `repo_relative` 折过的那种写法（出片时写进去的也是它），所以比较
    在一套换算里做完 —— 换算了再比，比各自折一半可靠。
    """
    paths: set[str] = set()
    copies: set[str] = set()
    for path in _ledgers(cfg):
        try:
            entries = load_entries(path)
        except ValueError:
            continue  # 某一本账坏了不该让整页白屏：它只是少算几张「用过」
        for entry in entries:
            source = entry.source
            if source is None or not source.image:
                continue
            paths.add(source.image)
            copies.add(Path(source.image).name)
    return _Used(paths=frozenset(paths), copies=frozenset(copies))


def _was_used(cfg: Config, asset: Asset, used: _Used) -> bool:
    """这张素材出过片没有。两种写法任一对上就算 —— 副本名对上 sha1 那一截。"""
    return repo_relative(cfg, asset.path) in used.paths or copy_name(asset) in used.copies
