"""素材索引与去重。

素材是**只读引用**：这里只读文件、算哈希，绝不写、不改、不搬。
索引落在本机（见 `config/default.toml` 的 `paths.asset_index`），不进 git。

去重三级收敛，一级比一级贵、一级比一级谨慎：

    1. 精确哈希（sha1）        —— 字节一致的同一张图
    2. 感知哈希（9×8 dhash）   —— 画面近似
    3. 灰度比对（128×128 MAE） —— 收口，避免把「只差一半亮度」当成「同一张」

第三级不是可选项：dhash 只看相邻两列的升降、不看绝对亮度，
所以纯「明暗不同、构图相同」的两张图在第二级会被误判成一对。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

__all__ = [
    "EXCLUDED_DIRS",
    "IMAGE_SUFFIXES",
    "Asset",
    "DuplicateGroup",
    "Index",
    "UnreadableImage",
    "build_index",
    "dhash_of",
    "find_duplicates",
    "load_index",
    "mean_absolute_error",
    "save_index",
    "scan",
]

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})

# 这些目录里装的是流程产物（比对拼图）或回收站，不是素材
EXCLUDED_DIRS = frozenset({"重复检查", ".Trash-0", ".Trash-1000", "$RECYCLE.BIN", ".git"})

DHASH_SIZE = (9, 8)
DHASH_MAX_DISTANCE = 4  # 与素材目录里那套现成脚本一致
MAE_SIZE = (128, 128)
MAE_THRESHOLD = 6.0  # 同上
INDEX_VERSION = 1


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
    """一组认为重复的图。kind 是 "exact" 或 "perceptual"。"""

    kind: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Index:
    roots: tuple[Path, ...]
    assets: tuple[Asset, ...]
    duplicates: tuple[DuplicateGroup, ...]
    problems: tuple[str, ...]
    built_at: str = ""

    @property
    def count(self) -> int:
        return len(self.assets)


# ---------- 哈希 ----------


def dhash_of(image: Image.Image) -> int:
    """9×8 灰度 dhash，逐行比较相邻两列的明暗，取 64 位。

    位序与素材目录里那套现成脚本一致，两边的结果可以直接对照。
    """
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


def _gray_bytes(path: Path) -> bytes:
    with Image.open(path) as image:
        return image.convert("L").resize(MAE_SIZE, Image.Resampling.LANCZOS).tobytes()


def mean_absolute_error(a: Path, b: Path) -> float:
    """两张图缩到 128×128 灰度后的平均像素差（0–255）。"""
    left = _gray_bytes(a)
    right = _gray_bytes(b)
    return sum(abs(x - y) for x, y in zip(left, right)) / len(left)


def _hamming(x: int, y: int) -> int:
    return (x ^ y).bit_count()


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- 扫描 ----------


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


class UnreadableImage(Exception):
    """这张图读不出来：坏文件、格式不认识、或者尺寸大到触发了保护。"""


def describe(path: Path, root: Path) -> Asset:
    """读一张图。读不出来就抛 UnreadableImage，由调用方记成问题。"""
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
    for root in roots:
        root = Path(root)
        if not root.exists():
            problems.append(
                f"素材根目录不存在：{root}（素材挪过位置就改 config/local.toml，然后重扫）"
            )
            continue
        for path in _iter_files(root):
            try:
                assets.append(describe(path, root))
            except UnreadableImage as exc:
                problems.append(f"读不出来，已跳过：{path} —— {exc}")
    assets.sort(key=lambda a: (str(a.root), str(a.path)))
    return assets, problems


# ---------- 去重 ----------


def find_duplicates(
    assets: Sequence[Asset],
    max_distance: int = DHASH_MAX_DISTANCE,
    mae_threshold: float = MAE_THRESHOLD,
) -> list[DuplicateGroup]:
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
    for position, anchor in enumerate(left):
        if anchor.path in claimed:
            continue
        bucket = [anchor]
        for other in left[position + 1 :]:
            if other.path in claimed:
                continue
            if _hamming(anchor.dhash, other.dhash) > max_distance:
                continue
            # 第三级收口：dhash 说像，还得画面真的像
            if mean_absolute_error(anchor.path, other.path) >= mae_threshold:
                continue
            bucket.append(other)
            claimed.add(other.path)
        if len(bucket) > 1:
            claimed.add(anchor.path)
            groups.append(
                DuplicateGroup(kind="perceptual", paths=tuple(str(a.path) for a in bucket))
            )

    return groups


# ---------- 索引 ----------


def build_index(roots: Sequence[Path]) -> Index:
    assets, problems = scan(roots)
    return Index(
        roots=tuple(Path(r) for r in roots),
        assets=tuple(assets),
        duplicates=tuple(find_duplicates(assets)),
        problems=tuple(problems),
        built_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def save_index(index: Index, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": INDEX_VERSION,
        "builtAt": index.built_at,
        "roots": [str(r) for r in index.roots],
        "count": index.count,
        "problems": list(index.problems),
        "duplicates": [{"kind": g.kind, "paths": list(g.paths)} for g in index.duplicates],
        "assets": [a.as_json() for a in index.assets],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_index(path: Path) -> Index:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Index(
        roots=tuple(Path(r) for r in data["roots"]),
        assets=tuple(Asset.from_json(a) for a in data["assets"]),
        duplicates=tuple(
            DuplicateGroup(kind=g["kind"], paths=tuple(g["paths"]))
            for g in data["duplicates"]
        ),
        problems=tuple(data["problems"]),
        built_at=data["builtAt"],
    )
