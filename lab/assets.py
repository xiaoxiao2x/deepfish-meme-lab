"""素材索引与去重。

素材是**只读引用**：这里只读文件、算哈希，绝不写、不改、不搬。
索引落在本机（见 `config/default.toml` 的 `paths.asset_index`），不进 git。

去重三级收敛，一级比一级贵、一级比一级谨慎：

    1. 精确哈希（sha1）        —— 字节一致的同一张图
    2. 感知哈希（9×8 dhash）   —— 画面近似
    3. 灰度比对（128×128 MAE） —— 收口，避免把「只差一半亮度」当成「同一张」

第三级不是可选项：dhash 只看相邻两列的升降、不看绝对亮度，
所以纯「明暗不同、构图相同」的两张图在第二级会被误判成一对。

第三级还分三档（口径同素材目录里的 `verify_dupes.py`）：
MAE < 6 判同一张、6–14 判「高度相似但不下结论」、≥ 14 判不同图。
中间那一档单列成 `suspects` 交给人看 —— 静默丢掉会把「同图的不同编码版」
一起扔掉，而那是需要人拿主意的取舍，不是脚本该替人做的。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .paths import iter_image_files

if TYPE_CHECKING:  # 只有做类型检查时才需要 Pillow —— 见文件末尾的说明
    from PIL import Image

__all__ = [
    "MAE_SAME",
    "MAE_SUSPECT",
    "Asset",
    "DuplicateGroup",
    "Index",
    "StaleIndex",
    "UnreadableImage",
    "build_index",
    "dhash_of",
    "find_duplicates",
    "gray_bytes",
    "load_index",
    "mean_absolute_error",
    "missing_from_disk",
    "save_index",
    "scan",
]

DHASH_SIZE = (9, 8)
DHASH_MAX_DISTANCE = 4  # 阈值同 find_dupes.py / verify_dupes.py
MAE_SIZE = (128, 128)
MAE_SAME = 6.0  # < 6：同一张图（同 verify_dupes.py）
MAE_SUSPECT = 14.0  # 6–14：高度相似，可疑但不下结论
INDEX_VERSION = 1


# ---------- 数据 ----------


@dataclass(frozen=True)
class Asset:
    """素材池里的一张图。图片本身不动，这里只有描述。"""

    path: Path
    root: Path
    width: int
    height: int
    bytes: int
    animated: bool
    sha1: str
    dhash: int

    def as_json(self) -> dict:
        return {
            "path": str(self.path),
            "root": str(self.root),
            "width": self.width,
            "height": self.height,
            "bytes": self.bytes,
            "animated": self.animated,
            "sha1": self.sha1,
            "dhash": self.dhash,
        }

    @classmethod
    def from_json(cls, data: dict) -> Asset:
        return cls(
            path=Path(data["path"]),
            root=Path(data["root"]),
            width=data["width"],
            height=data["height"],
            bytes=data["bytes"],
            animated=data["animated"],
            sha1=data["sha1"],
            dhash=data["dhash"],
        )


@dataclass(frozen=True)
class DuplicateGroup:
    """一组重复嫌疑。

    kind：
        exact      —— 字节一致
        perceptual —— 哈希像、画面也像（MAE < 6），判定为一组
        suspect    —— 哈希像，但 MAE 落在 6–14，需要人看
    """

    kind: str
    paths: tuple[str, ...]
    mae: float | None = None


@dataclass(frozen=True)
class Index:
    roots: tuple[Path, ...]
    assets: tuple[Asset, ...]
    duplicates: tuple[DuplicateGroup, ...]
    problems: tuple[str, ...]
    built_at: str = ""
    #: 第二级（dhash）认可的对数、其中被第三级（MAE）拦下的对数与被拦对的 MAE 区间。
    #: 留着是为了让「收口到底拦掉了什么」看得见 —— 只看留下来的组，证明不了没误杀。
    candidates: int = 0
    vetoed: int = 0
    vetoed_mae: tuple[float, float] | None = None

    @property
    def count(self) -> int:
        return len(self.assets)

    @property
    def suspects(self) -> tuple[DuplicateGroup, ...]:
        return tuple(g for g in self.duplicates if g.kind == "suspect")

    @property
    def duplicate_paths(self) -> frozenset[str]:
        return frozenset(
            path for group in self.duplicates if group.kind != "suspect" for path in group.paths
        )

    @property
    def unique(self) -> tuple[Asset, ...]:
        """不在任何重复组里的素材 —— 去重要回答的另一半。"""
        taken = self.duplicate_paths
        return tuple(a for a in self.assets if str(a.path) not in taken)


class UnreadableImage(Exception):
    """这张图读不出来：坏文件、格式不认识、或者尺寸大到触发了保护。"""


class StaleIndex(Exception):
    """索引是别的版本写的，或者已经对不上现在的素材了。"""


# ---------- 哈希 ----------


def dhash_of(image: Image.Image) -> int:
    """9×8 灰度 dhash，逐行比较相邻两列的明暗，取 64 位。

    位序与素材目录里那套现成脚本一致（同一套 `row[x] > row[x+1]` 与 `1 << index`），
    但**重采样算法不同**（这里用 LANCZOS，旧脚本用 Pillow 默认的 BICUBIC 或
    ImageMagick），所以同一个文件在两边的哈希值可能差几位。阈值恰好是汉明距离
    ≤ 4，差几位足以翻转判定 —— 可以对照判定结果，不要逐位对照哈希值。
    """
    from PIL import Image

    width, height = DHASH_SIZE
    small = image.convert("L").resize(DHASH_SIZE, Image.Resampling.LANCZOS)
    pixels = small.tobytes()
    bits = 0
    index = 0
    for y in range(height):
        row = pixels[y * width : (y + 1) * width]
        for x in range(width - 1):
            if row[x] > row[x + 1]:
                bits |= 1 << index
            index += 1
    return bits


@lru_cache(maxsize=1024)
def _gray_bytes(key: str) -> bytes:
    from PIL import Image

    with Image.open(key) as image:
        return image.convert("L").resize(MAE_SIZE, Image.Resampling.LANCZOS).tobytes()


def gray_bytes(path: Path) -> bytes:
    """缩到 128×128 的灰度像素。按路径缓存 —— 去重的二重循环会反复要同一张。"""
    return _gray_bytes(str(path))


def mean_absolute_error(a: Path, b: Path) -> float:
    """两张图缩到 128×128 灰度后的平均像素差（0–255）。"""
    left = gray_bytes(Path(a))
    right = gray_bytes(Path(b))
    # strict=False：长度不一致时按短的截断（沿用原来的行为），只把这件事写明。
    return sum(abs(x - y) for x, y in zip(left, right, strict=False)) / len(left)


def _hamming(x: int, y: int) -> int:
    return (x ^ y).bit_count()


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- 扫描 ----------


def describe(path: Path, root: Path) -> Asset:
    """读一张图。读不出来就抛 UnreadableImage，由调用方记成问题。"""
    from PIL import Image

    try:
        with Image.open(path) as image:
            width, height = image.size
            animated = getattr(image, "n_frames", 1) > 1
            digest = dhash_of(image)
    except Exception as exc:
        raise UnreadableImage(f"{type(exc).__name__}: {exc}") from exc
    return Asset(
        path=path,
        root=root,
        width=width,
        height=height,
        bytes=path.stat().st_size,
        animated=animated,
        sha1=_sha1(path),
        dhash=digest,
    )


def scan(roots: Sequence[Path]) -> tuple[list[Asset], list[str]]:
    """扫描素材根目录。返回 (素材清单, 问题清单)——根目录失联是问题，不是崩溃。"""
    assets: list[Asset] = []
    problems: list[str] = []

    if not roots:
        problems.append(
            "没配置素材根目录：paths.asset_roots 是空的，"
            "先把素材目录写进 config/local.toml"
        )
        return assets, problems

    for root in roots:
        root = Path(root)
        if not root.exists():
            problems.append(
                f"素材根目录不存在：{root}（素材挪过位置就改 config/local.toml，然后重扫）"
            )
            continue
        for path in iter_image_files(root):
            try:
                assets.append(describe(path, root))
            except UnreadableImage as exc:
                problems.append(f"读不出来，已跳过：{path} —— {exc}")

    assets.sort(key=lambda a: (str(a.root), str(a.path)))
    return assets, problems


def _fingerprint(roots: Sequence[Path]) -> dict[str, tuple[float, int]]:
    """素材的 mtime + size 快照，用来证明扫描没碰过它们。"""
    prints: dict[str, tuple[float, int]] = {}
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for path in iter_image_files(root):
            stat = path.stat()
            prints[str(path)] = (stat.st_mtime, stat.st_size)
    return prints


# ---------- 去重 ----------


def find_duplicates(
    assets: Sequence[Asset],
    max_distance: int = DHASH_MAX_DISTANCE,
    mae_same: float = MAE_SAME,
    mae_suspect: float = MAE_SUSPECT,
) -> tuple[list[DuplicateGroup], int, list[float]]:
    """返回 (分组, 第二级候选对数, 被第三级拦下的 MAE)。

    拦下的那些也返回出来 —— 「留下来的确实像」证明不了「丢掉的不是重复」，
    要看收口到底拦了什么。
    """
    groups: list[DuplicateGroup] = []

    by_digest: dict[str, list[Asset]] = {}
    for asset in assets:
        by_digest.setdefault(asset.sha1, []).append(asset)

    claimed: set[Path] = set()
    for bucket in by_digest.values():
        if len(bucket) > 1:
            groups.append(
                DuplicateGroup(
                    kind="exact", paths=tuple(sorted(str(a.path) for a in bucket))
                )
            )
            claimed.update(a.path for a in bucket)

    left = [a for a in assets if a.path not in claimed]
    candidates = 0
    vetoed: list[float] = []

    for position, anchor in enumerate(left):
        if anchor.path in claimed:
            continue
        bucket = [anchor]
        for other in left[position + 1 :]:
            if other.path in claimed:
                continue
            if _hamming(anchor.dhash, other.dhash) > max_distance:
                continue
            candidates += 1
            distance = mean_absolute_error(anchor.path, other.path)
            if distance >= mae_suspect:
                vetoed.append(distance)  # 不同图
                continue
            if distance >= mae_same:
                vetoed.append(distance)
                groups.append(
                    DuplicateGroup(
                        kind="suspect",
                        paths=(str(anchor.path), str(other.path)),
                        mae=round(distance, 2),
                    )
                )
                continue
            bucket.append(other)
            claimed.add(other.path)

        if len(bucket) > 1:
            claimed.add(anchor.path)
            groups.append(
                DuplicateGroup(kind="perceptual", paths=tuple(str(a.path) for a in bucket))
            )

    return groups, candidates, vetoed


# ---------- 索引 ----------


def build_index(roots: Sequence[Path], verify_readonly: bool = False) -> Index:
    """扫一遍并组装索引。

    `verify_readonly` 开着时，扫描前后各拍一次素材的 mtime/size，对不上就记成问题
    —— 「只读」这条承诺要能自己证明，不能只在提交信息里说一次。
    """
    before = _fingerprint(roots) if verify_readonly else None
    assets, problems = scan(roots)
    if before is not None and before != _fingerprint(roots):
        problems.append(
            "扫描过程中素材被改动过（mtime 或 size 变了）—— 扫描不该写素材，请报告这个 bug"
        )

    groups, candidates, vetoed = find_duplicates(assets)
    return Index(
        roots=tuple(Path(r) for r in roots),
        assets=tuple(assets),
        duplicates=tuple(groups),
        problems=tuple(problems),
        built_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        candidates=candidates,
        vetoed=len(vetoed),
        vetoed_mae=(round(min(vetoed), 2), round(max(vetoed), 2)) if vetoed else None,
    )


def save_index(index: Index, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "builtAt": index.built_at,
        "roots": [str(r) for r in index.roots],
        "count": index.count,
        "uniqueCount": len(index.unique),
        "problems": list(index.problems),
        "candidates": index.candidates,
        "vetoed": index.vetoed,
        "vetoedMae": list(index.vetoed_mae) if index.vetoed_mae else None,
        "duplicates": [
            {"kind": g.kind, "paths": list(g.paths), "mae": g.mae} for g in index.duplicates
        ],
        "assets": [a.as_json() for a in index.assets],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def load_index(path: Path) -> Index:
    """读索引。文件坏了抛 ValueError 系，版本不对抛 StaleIndex。"""
    data: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    version = data.get("version")
    if version != INDEX_VERSION:
        raise StaleIndex(
            f"索引版本是 {version}，这个工作台认的是 {INDEX_VERSION} —— "
            "重跑 python -m lab assets scan"
        )
    vetoed_mae = data.get("vetoedMae")
    return Index(
        roots=tuple(Path(r) for r in data["roots"]),
        assets=tuple(Asset.from_json(a) for a in data["assets"]),
        duplicates=tuple(
            DuplicateGroup(kind=g["kind"], paths=tuple(g["paths"]), mae=g.get("mae"))
            for g in data["duplicates"]
        ),
        problems=tuple(data["problems"]),
        built_at=data["builtAt"],
        candidates=data.get("candidates", 0),
        vetoed=data.get("vetoed", 0),
        vetoed_mae=tuple(vetoed_mae) if vetoed_mae else None,
    )


def missing_from_disk(index: Index) -> list[str]:
    """索引里记着、但磁盘上已经不在的素材 —— 素材挪过位置的信号。"""
    return [str(a.path) for a in index.assets if not a.path.exists()]
