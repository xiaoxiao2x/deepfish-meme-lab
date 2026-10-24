"""保留：把挑中的那张从当天的记录搬进配方库（票 #11，ADR 0003）。

两本账的分工在这个文件里最清楚：出片时写的那本（output/<日期>/registry.local.json）
只在本机、只活当天；`keep` 是那个**动作** —— 把一条从左边搬到 `data/registry.json`，
从那以后它才算定稿。搬的不是一张图，是一次"什么参数能出什么图"。

所以这里验的不是"文件写没写进去"，而是**搬过去的那条还答得出原来那些问题**：
种子、步数、模型、模板、引擎版本。搬丢一样，配方库就退化成一张图片清单。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from lab.config import load_config
from lab.keeping import IMPORTED_KIND, find_entry, gallery, keep, kind_of
from lab.recording import UNKNOWN_VERSION
from lab.registry import Entry, library_path, load_entries, record_path, write_entry

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 24)


def _cfg(tmp_path: Path):
    """真配置 + 仓库根与输出目录都挪到 tmp —— 记录里的 file 才会是相对路径。"""
    cfg = load_config(REPO_ROOT)
    return replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
    )


def picture(folder: Path, name: str = "fish-001.orig.png") -> Path:
    """一张真 PNG，落在给定目录里。宽高要能被读出来，所以不能用几个字节糊弄。"""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    Image.new("RGB", (64, 48), (10, 20, 30)).save(path, format="PNG")
    return path


def entry(**overrides) -> Entry:
    base = Entry(
        id="fish-001",
        created_at="2026-10-24T02:05:43+08:00",
        kind="orig",
        file="output/2026-10-24/fish-001.orig.png",
        width=1024,
        height=1024,
        workflow="workflows/fish-txt2img.json",
        model="flux-2-klein-base-4b-fp8.safetensors",
        prompt="chibi blue whale girl",
        negative="lowres",
        seed=12345,
        steps=20,
        cfg=5.0,
        comfyui_version="0.3.40",
    )
    return replace(base, **overrides)


def ledger(cfg, **overrides) -> Path:
    """把一条记录写进"当天那本账"，返回账本路径。"""
    path = record_path(cfg.paths.output_dir, TODAY)
    write_entry(path, entry(**overrides))
    return path


# ---------- 搬过去的那条还是原来那条 ----------


def test_keep_copies_the_whole_recipe_into_the_library(tmp_path):
    """验收判据 2：`keep` 把条目写进配方库 —— 生成参数一样不少地跟着过去。"""
    cfg = _cfg(tmp_path)
    image = picture(cfg.paths.output_dir / TODAY.isoformat())
    ledger(cfg)

    kept, traced = keep(cfg, image, today=TODAY)

    assert traced, "账就在图旁边，没找到来路说明这一步白做了"
    (saved,) = load_entries(library_path(tmp_path))
    assert saved == kept
    assert (saved.seed, saved.steps, saved.cfg) == (12345, 20, 5.0)
    assert saved.model == "flux-2-klein-base-4b-fp8.safetensors"
    assert saved.workflow == "workflows/fish-txt2img.json"
    assert saved.prompt == "chibi blue whale girl"
    assert saved.comfyui_version == "0.3.40", "引擎版本是复现的前提，不能丢"


def test_the_library_entry_points_at_the_picture_relative_to_the_repo(tmp_path):
    """落点写成相对仓库根：这台机器上的绝对路径搬去别处就指错地方了。"""
    cfg = _cfg(tmp_path)
    image = picture(cfg.paths.output_dir / TODAY.isoformat())
    ledger(cfg)

    kept, _ = keep(cfg, image, today=TODAY)

    assert kept.file == "output/2026-10-24/fish-001.orig.png"
    assert not Path(kept.file).is_absolute()


def test_a_picture_outside_the_repo_folds_home_into_a_tilde(tmp_path, monkeypatch):
    """仓库外的图折成 `~/...`：配方库要进 git，`/home/<用户名>/...` 等于把本机布局带进去。"""
    monkeypatch.setenv("HOME", str(tmp_path / "家"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "家"))
    cfg = _cfg(tmp_path / "仓库")
    picture(tmp_path / "家" / "收藏", name="IMG-2024.jpg")
    outsider = tmp_path / "家" / "收藏" / "IMG-2024.jpg"

    kept, _ = keep(cfg, outsider, today=TODAY)

    assert kept.file == "~/收藏/IMG-2024.jpg"
    assert str(tmp_path) not in kept.file, "本机绝对路径不该进配方库"


def test_keep_is_idempotent(tmp_path):
    """同一张留两次，配方库里还是一条 —— 重复留不该堆出两条互相打架的。"""
    cfg = _cfg(tmp_path)
    image = picture(cfg.paths.output_dir / TODAY.isoformat())
    ledger(cfg)

    keep(cfg, image, today=TODAY)
    keep(cfg, image, today=TODAY)

    assert len(load_entries(library_path(tmp_path))) == 1


def test_each_stage_of_the_same_id_is_its_own_entry(tmp_path):
    """fish-001 的原图和它的贴纸都要能留 —— 一个号两个面，各有各的参数。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    original = picture(folder)
    sticker = picture(folder, name="fish-001.sticker.png")
    ledger(cfg)
    write_entry(
        record_path(cfg.paths.output_dir, TODAY),
        entry(kind="sticker", file="fish-001.sticker.png", seed=0, workflow="workflows/fish-sticker.json"),
    )

    keep(cfg, original, today=TODAY)
    keep(cfg, sticker, today=TODAY)

    assert [one.kind for one in load_entries(library_path(tmp_path))] == [
        "orig",
        "sticker",
    ]


# ---------- 图挪走了，来路还找得回来 ----------


def test_keep_finds_the_recipe_after_the_picture_moved_elsewhere(tmp_path):
    """图被挪进收藏夹再来 keep：图旁边没账了，但当天那本还躺着，照样找得回参数。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    ledger(cfg)
    keep_dir = tmp_path / "收藏"
    keep_dir.mkdir()
    moved = keep_dir / "fish-001.orig.png"
    moved.write_bytes(picture(folder).read_bytes())

    kept, traced = keep(cfg, moved, today=TODAY)

    assert traced
    assert kept.seed == 12345
    assert kept.file == "收藏/fish-001.orig.png", "落点得跟着图走，不是跟着旧记录"


def test_a_record_pointing_into_the_home_folder_still_resolves(tmp_path, monkeypatch):
    """记录里的落点可能是 `~/...`（仓库外但在家目录里那种）。

    直接把它拼到仓库根上是拼不出东西的，`~/收藏/fish-001.orig.png` 会变成一个
    根本不存在的路径 —— 路径那一轮就白比了，白退到（号，阶段）那一步。
    """
    cfg = _cfg(tmp_path)
    home = tmp_path / "家"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    image = picture(home / "收藏")
    entries = [entry(file="~/收藏/fish-001.orig.png")]

    assert find_entry(cfg, entries, image) is not None


def test_find_entry_falls_back_to_the_serial_and_stage(tmp_path):
    """路径对不上时退到（号，阶段）：账里记的落点被手工改过也还能认出来。"""
    cfg = _cfg(tmp_path)
    image = picture(tmp_path / "别处", name="fish-002.orig.png")
    entries = [entry(id="fish-002", file="某个/被改过的/路径.png")]

    found = find_entry(cfg, entries, image)

    assert found is not None and found.id == "fish-002"


def test_find_entry_does_not_match_a_different_stage(tmp_path):
    """同一个号的贴纸不该被当成原图 —— 参数是两套。"""
    cfg = _cfg(tmp_path)
    image = picture(tmp_path / "别处", name="fish-002.sticker.png")
    entries = [entry(id="fish-002", kind="orig", file="某个/被改过的/路径.png")]

    assert find_entry(cfg, entries, image) is None


# ---------- 没有来路时说实话 ----------


def test_keeping_a_picture_without_a_record_still_works_but_says_so(tmp_path):
    """外来的图没有来路：记下这张图本身，生成那几栏留空，而且当场说明白。"""
    cfg = _cfg(tmp_path)
    outsider = picture(tmp_path / "收藏", name="IMG-2024.jpg")

    kept, traced = keep(cfg, outsider, today=TODAY)

    assert not traced, "账里没有它却报有来路，就是在骗人"
    assert kept.id == "IMG-2024", "外来图用文件名当号，空号会互相盖掉"
    assert kept.kind == IMPORTED_KIND, "阶段留空会在 gallery 里留一格空白，也不在 schema 的枚举里"
    assert (kept.width, kept.height) == (64, 48)
    assert kept.comfyui_version == UNKNOWN_VERSION
    assert (kept.seed, kept.steps, kept.cfg) == (0, 0, 0.0)
    assert load_entries(library_path(tmp_path)) == [kept]


def test_keeping_a_missing_picture_explains_what_is_wrong(tmp_path):
    with pytest.raises(ValueError) as caught:
        keep(_cfg(tmp_path), tmp_path / "没有.png", today=TODAY)

    assert "不在" in str(caught.value)


def test_kind_of_reads_the_stage_out_of_the_name(tmp_path):
    assert kind_of(Path("fish-007.sticker.png")) == "sticker"
    assert kind_of(Path("fish-007.captioned.png")) == "captioned"
    assert kind_of(Path("IMG-2024.jpg")) == ""


def test_kind_of_ignores_names_that_only_look_like_a_stage(tmp_path):
    """文件名里带了几个点不代表它是工作台出的 —— 认不出来就当没来路（imported）。

    照搬"倒数第二段就是阶段"的话，`screenshot.2026.10.24.png` 会得到 `kind="24"`，
    这个值会一路写进进 git 的配方库，成为一条谁也读不懂的阶段。
    """
    assert kind_of(Path("screenshot.2026.10.24.png")) == ""
    assert kind_of(Path("cover.final.png")) == ""
    assert kind_of(Path("a.b.png")) == ""


def test_keeping_a_familiar_looking_name_still_lands_as_imported(tmp_path):
    cfg = _cfg(tmp_path)
    source = picture(tmp_path, name="screenshot.2026.10.24.png")

    kept, traced = keep(cfg, source, today=TODAY)

    assert not traced
    assert kept.kind == IMPORTED_KIND, "认不出阶段的图不能带个假阶段进配方库"
    assert kept.id == "screenshot.2026.10.24"


def test_two_pictures_with_the_same_name_do_not_overwrite_each_other(tmp_path):
    """同一天留两张同名但不同路的图，得都留下。

    外来图的号是文件名，而文件名不是身份：配方库按（号，阶段，日期）去重，
    两个目录下各有一张 `cover.png` 时两条会撞成同一个 key，后写的那条把前一条
    静默盖掉 —— 库里的图少了一张，而人以为留了两张。
    """
    cfg = _cfg(tmp_path)
    first = picture(tmp_path / "甲", name="cover.png")
    second = picture(tmp_path / "乙", name="cover.png")

    one, _ = keep(cfg, first, today=TODAY)
    two, _ = keep(cfg, second, today=TODAY)

    assert one.id == "cover"
    assert two.id == "cover-2", "撞号得让开，不能让后来那条顶掉前一条"
    kept = load_entries(library_path(tmp_path))
    assert {one.file for one in kept} == {"甲/cover.png", "乙/cover.png"}


def test_keeping_the_same_picture_twice_stays_idempotent(tmp_path):
    """同一张外来图留两遍仍然只有一条 —— 换号只该发生在真的撞了另一张图时。"""
    cfg = _cfg(tmp_path)
    source = picture(tmp_path / "收藏", name="cover.png")

    keep(cfg, source, today=TODAY)
    keep(cfg, source, today=TODAY)

    kept = load_entries(library_path(tmp_path))
    assert [one.id for one in kept] == ["cover"]


# ---------- 表格（验收判据 3） ----------


def test_gallery_lists_what_was_kept_newest_first(tmp_path):
    """`gallery` 是"我刚留了什么"的视角：最近留的排最前。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    ledger(cfg, id="fish-001", created_at="2026-10-24T02:05:43+08:00")
    write_entry(
        record_path(cfg.paths.output_dir, TODAY),
        entry(id="fish-002", created_at="2026-10-24T03:00:00+08:00", file="fish-002.orig.png"),
    )

    keep(cfg, picture(folder, name="fish-002.orig.png"), today=TODAY)
    keep(cfg, picture(folder, name="fish-001.orig.png"), today=TODAY)

    assert [one.id for one in gallery(cfg)] == ["fish-002", "fish-001"]


def test_gallery_of_an_empty_library_is_empty(tmp_path):
    assert gallery(_cfg(tmp_path)) == []


def test_gallery_refuses_to_pretend_a_broken_library_is_empty(tmp_path):
    """配方库坏了就报错：当成空的会把攒下来的定稿全吞掉。"""
    path = library_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{不是 JSON", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        gallery(_cfg(tmp_path))

    assert "JSON" in str(caught.value)

# ---------- 命令行（验收判据 2 与 3 的对外形状） ----------


def _width(text: str) -> int:
    """一行表格的显示宽度 —— 一个汉字占两格，不能拿 len 当宽度。"""
    import unicodedata

    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _twin_repo(tmp_path: Path) -> Path:
    """造一个小仓库：真配置，输出目录跟着仓库走。

    不抽到 conftest：这个文件要的是"一个能 load_config 的根"，别处要的是
    "一个连着假引擎的根"，两件事合起来反而更绕。
    """
    import shutil

    root = tmp_path / "repo"
    (root / "config").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "config" / "default.toml", root / "config" / "default.toml")
    return root


def test_cli_keep_prints_where_the_recipe_went(tmp_path, capsys):
    """命令行是这张票的对外接口：留完得说清留在哪、落点在哪。"""
    from lab.cli import main

    root = _twin_repo(tmp_path)
    cfg = load_config(root)
    folder = cfg.paths.output_dir / date.today().isoformat()
    image = picture(folder)
    write_entry(
        record_path(cfg.paths.output_dir, date.today()),
        entry(file=image.relative_to(root).as_posix()),
    )

    code = main(["keep", str(image)], repo_root=root)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "已留：fish-001.orig" in out
    assert "当天的记录" in out
    assert str(library_path(root)) in out


def test_cli_gallery_lists_the_library(tmp_path, capsys):
    from lab.cli import main

    root = _twin_repo(tmp_path)
    cfg = load_config(root)
    folder = cfg.paths.output_dir / date.today().isoformat()
    image = picture(folder)
    write_entry(
        record_path(cfg.paths.output_dir, date.today()),
        entry(file=image.relative_to(root).as_posix()),
    )
    assert main(["keep", str(image)], repo_root=root) == 0
    capsys.readouterr()

    code = main(["gallery"], repo_root=root)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "鱼片加工厂 · 配方库" in out
    assert "fish-001" in out
    assert "12345" in out, "种子得看得见 —— 判据 3 要的就是这一栏"


def test_cli_gallery_keeps_the_columns_aligned_for_long_ids(tmp_path, capsys):
    """号列也得截：外来的图拿文件名当号，长起来会把后面几列整张表顶歪。"""
    from lab.cli import main

    root = _twin_repo(tmp_path)
    image = picture(tmp_path, name="screenshot.2026.10.24.png")
    assert main(["keep", str(image)], repo_root=root) == 0
    capsys.readouterr()

    code = main(["gallery"], repo_root=root)

    out = capsys.readouterr().out
    assert code == 0, out
    lines = out.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("号"))
    row = lines[header + 1]
    assert "screensho…" in row, f"号列该截断：{row}"
    assert _width(row) == _width(lines[header]), f"表头和行不一样宽：{lines[header]!r} / {row!r}"


def test_cli_gallery_of_an_empty_library_sends_you_to_keep(tmp_path, capsys):
    from lab.cli import main

    code = main(["gallery"], repo_root=_twin_repo(tmp_path))

    out = capsys.readouterr().out
    assert code == 0, out
    assert "还没留东西" in out
    assert "lab keep" in out, "空库不是死路：得告诉人下一步是哪条命令"


def test_cli_takes_repo_root_before_or_after_the_subcommand(tmp_path, capsys):
    """两个位置都得认：`lab --repo-root X gallery` 与 `lab gallery --repo-root X`。

    参数摆哪儿是肌肉记忆的事，让人记「只能在子命令后面」是白添一道坎。
    """
    from lab.cli import main

    root = _twin_repo(tmp_path)

    assert main(["--repo-root", str(root), "gallery"]) == 0
    assert "还没留东西" in capsys.readouterr().out

    assert main(["gallery", "--repo-root", str(root)]) == 0
    assert "还没留东西" in capsys.readouterr().out


def test_cli_takes_repo_root_from_the_environment(tmp_path, capsys, monkeypatch):
    """子进程里（脚本、验收）用环境变量比拼命令行省事。"""
    from lab.cli import main

    root = _twin_repo(tmp_path)
    monkeypatch.setenv("LAB_REPO_ROOT", str(root))

    assert main(["gallery"]) == 0
    assert "还没留东西" in capsys.readouterr().out


def test_cli_repo_root_beats_the_environment(tmp_path, capsys, monkeypatch):
    """两处都指了的时候听命令行的 —— 否则没法在一次会话里临时切仓库。"""
    from lab.cli import main

    root = _twin_repo(tmp_path)
    monkeypatch.setenv("LAB_REPO_ROOT", str(tmp_path / "nope"))

    assert main(["gallery", "--repo-root", str(root)]) == 0
    assert "还没留东西" in capsys.readouterr().out


def test_cli_keeps_the_picture_in_the_repo_it_was_pointed_at(tmp_path, capsys):
    """指了哪个仓库就往哪个仓库写 —— 验临时仓库时不许碰真仓库。"""
    from lab.cli import main
    from lab.registry import library_path, load_entries

    root = _twin_repo(tmp_path)
    cfg = load_config(root)
    image = picture(cfg.paths.output_dir / date.today().isoformat())

    assert main(["keep", str(image), "--repo-root", str(root)]) == 0
    capsys.readouterr()

    kept = load_entries(library_path(root))
    assert [one.id for one in kept] == ["fish-001"]

