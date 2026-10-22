"""素材索引与去重的行为规格。

素材是只读引用：扫描不写、不改、不搬。
去重三级收敛：精确哈希（字节一致）→ 感知哈希（画面近似）→ 灰度比对（收口，避免误杀）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

from lab.assets import (
    build_index,
    load_index,
    mean_absolute_error,
    save_index,
)
from lab.cli import main

REPO_DEFAULT_TOML = (
    Path(__file__).resolve().parent.parent / "config" / "default.toml"
)

# ---------- 造图工具 ----------


def solid(path, color=(120, 40, 200), size=(32, 32)):
    Image.new("RGB", size, color).save(path)
    return path


def half(path, dark=0, size=(64, 64)):
    """左半暗、右半亮。

    只改「暗那一半」的亮度，能造出「dhash 一样、画面差很多」的一对图——
    因为 dhash 只看相邻两列的升降，不看绝对亮度。正好用来验第三级收口。
    """
    im = Image.new("L", size)
    px = im.load()
    mid = size[0] // 2
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = 255 if x >= mid else dark
    im.convert("RGB").save(path)
    return path


def animated_gif(path, size=(16, 16)):
    frames = [Image.new("RGB", size, c) for c in ("#ff0000", "#0000ff")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


def pool(tmp_path):
    root = tmp_path / "pool"
    root.mkdir()
    return root


# ---------- 扫描 ----------


def test_index_records_shape_size_and_hashes(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png", size=(10, 20))

    index = build_index([root])

    (asset,) = index.assets
    assert (asset.width, asset.height) == (10, 20)
    assert asset.bytes > 0
    assert len(asset.sha1) == 40
    assert isinstance(asset.dhash, int)


def test_only_images_are_indexed(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png")
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")

    index = build_index([root])

    assert [a.path.name for a in index.assets] == ["a.png"]


def test_housekeeping_dirs_are_skipped(tmp_path):
    root = pool(tmp_path)
    solid(root / "keep.png")
    for junk in ("重复检查", ".Trash-0"):
        d = root / junk
        d.mkdir()
        solid(d / "old.png")

    index = build_index([root])

    assert [a.path.name for a in index.assets] == ["keep.png"]


def test_animated_gif_is_flagged(tmp_path):
    root = pool(tmp_path)
    animated_gif(root / "a.gif")

    index = build_index([root])

    (asset,) = index.assets
    assert asset.animated


def test_still_png_is_not_flagged_as_animated(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png")

    index = build_index([root])

    (asset,) = index.assets
    assert not asset.animated


def test_subdirectories_are_walked(tmp_path):
    root = pool(tmp_path)
    nested = root / "合集"
    nested.mkdir()
    solid(nested / "a.png")

    index = build_index([root])

    assert [a.path.name for a in index.assets] == ["a.png"]


def test_missing_root_is_a_problem_not_an_error(tmp_path):
    index = build_index([tmp_path / "nope"])

    assert index.assets == ()
    assert len(index.problems) == 1
    assert "nope" in index.problems[0]


def test_two_roots_are_merged_into_one_index(tmp_path):
    first = pool(tmp_path)
    second = tmp_path / "second"
    second.mkdir()
    solid(first / "a.png")
    solid(second / "b.png")

    index = build_index([first, second])

    assert sorted(a.path.name for a in index.assets) == ["a.png", "b.png"]
    assert len(index.roots) == 2


# ---------- 去重 ----------


def test_byte_identical_files_group_as_exact(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png")
    solid(root / "b.png")  # 同参数 → 同字节

    index = build_index([root])

    (group,) = [g for g in index.duplicates if g.kind == "exact"]
    assert len(group.paths) == 2


def test_unrelated_images_do_not_group(tmp_path):
    """dhash 相同的两张图（只差一半亮度）不该被合并——第三级收口把它们分开。"""
    root = pool(tmp_path)
    half(root / "dark.png", dark=0)
    half(root / "light.png", dark=200)

    index = build_index([root])

    assert index.duplicates == ()


def test_resized_copy_groups_as_perceptual(tmp_path):
    root = pool(tmp_path)
    src = half(root / "a.png", dark=0, size=(64, 64))
    Image.open(src).resize((32, 32)).save(root / "b.png")

    index = build_index([root])

    (group,) = [g for g in index.duplicates if g.kind == "perceptual"]
    assert len(group.paths) == 2


def test_mean_absolute_error_is_zero_for_the_same_image(tmp_path):
    p = half(tmp_path / "a.png")

    assert mean_absolute_error(p, p) == 0.0


def test_mean_absolute_error_is_large_for_a_repainted_image(tmp_path):
    a = half(tmp_path / "a.png", dark=0)
    b = half(tmp_path / "b.png", dark=200)

    # 一半的像素（暗的那半）差 200，理论均值 100；交界处被插值抹掉一点点，
    # 所以留一档余量。阈值只关心数量级：远大于 MAE_THRESHOLD(6)。
    assert mean_absolute_error(a, b) > 90


# ---------- 索引落盘 ----------


def test_index_roundtrips_through_json(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png")
    solid(root / "b.png")
    index = build_index([root])
    path = tmp_path / "index.local.json"

    save_index(index, path)
    again = load_index(path)

    assert again.assets == index.assets
    assert again.duplicates == index.duplicates
    assert again.roots == index.roots


def test_saved_index_records_when_it_was_built(tmp_path):
    root = pool(tmp_path)
    solid(root / "a.png")
    path = tmp_path / "index.local.json"

    save_index(build_index([root]), path)

    assert load_index(path).built_at  # 非空


# ---------- 命令行 ----------


def make_repo(tmp_path, *roots) -> Path:
    """造一个最小仓库：真的 config/default.toml + 只声明了素材根的 local.toml。"""
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copy(REPO_DEFAULT_TOML, repo / "config" / "default.toml")
    listed = ", ".join(f'"{r}"' for r in roots)
    (repo / "config" / "local.toml").write_text(
        f"[paths]\nasset_roots = [{listed}]\n", encoding="utf-8"
    )
    return repo


def test_assets_scan_writes_an_index_and_reports_the_count(tmp_path, capsys):
    root = pool(tmp_path)
    solid(root / "a.png")
    solid(root / "b.png")
    repo = make_repo(tmp_path, root)

    code = main(["assets", "scan"], repo_root=repo)

    out = capsys.readouterr().out
    assert code == 0
    assert "2 张" in out
    assert (repo / "assets" / "index.local.json").exists()


def test_assets_scan_exits_nonzero_when_a_root_is_missing(tmp_path, capsys):
    repo = make_repo(tmp_path, tmp_path / "gone")

    code = main(["assets", "scan"], repo_root=repo)

    assert code == 1
    assert "gone" in capsys.readouterr().out


def test_assets_dupes_lists_groups_from_the_index(tmp_path, capsys):
    root = pool(tmp_path)
    solid(root / "a.png")
    solid(root / "b.png")
    repo = make_repo(tmp_path, root)
    main(["assets", "scan"], repo_root=repo)
    capsys.readouterr()

    code = main(["assets", "dupes"], repo_root=repo)

    out = capsys.readouterr().out
    assert code == 0
    assert "字节一致" in out


def test_assets_dupes_without_an_index_says_how_to_make_one(tmp_path, capsys):
    repo = make_repo(tmp_path)

    code = main(["assets", "dupes"], repo_root=repo)

    assert code == 2
    assert "scan" in capsys.readouterr().err


def test_assets_without_an_action_prints_usage(tmp_path, capsys):
    repo = make_repo(tmp_path)

    code = main(["assets"], repo_root=repo)

    assert code == 2
    assert "scan" in capsys.readouterr().err
