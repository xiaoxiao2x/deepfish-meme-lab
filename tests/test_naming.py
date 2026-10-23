"""成品的落点与编号：叫什么号、落哪个目录。

原图、贴纸、带字成图三站共用这套规则（序号是同一个配方条目的 id，靠后缀区分），
所以它自己一个模块、自己一组用例 —— 谁改坏了这里，三站会一起错位。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from lab.config import load_config
from lab.naming import daily_dir, next_serial, parse_serial, plan_output

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 23)


def _cfg(tmp_path: Path):
    """真配置 + 把输出目录换到 tmp 里 —— 用例不往仓库的 output/ 里写东西。"""
    cfg = load_config(REPO_ROOT)
    return replace(cfg, paths=replace(cfg.paths, output_dir=tmp_path / "output"))


def test_next_serial_counts_every_kind(tmp_path):
    """fish-001 是一个配方条目，原图/贴纸/带字共用序号，不该分后缀数。"""
    folder = tmp_path / "output" / "2026-10-23"
    folder.mkdir(parents=True)
    (folder / "fish-003.sticker.png").write_bytes(b"x")

    assert next_serial(folder) == 4


def test_next_serial_is_one_for_a_missing_folder(tmp_path):
    assert next_serial(tmp_path / "nope") == 1


def test_next_serial_skips_numbers_the_record_already_used(tmp_path):
    """图被删了，号不能回收 —— 记录里 fish-007 还躺着，下一个就该是 008。"""
    folder = tmp_path / "output" / "2026-10-23"
    folder.mkdir(parents=True)
    (folder / "fish-003.orig.png").write_bytes(b"x")

    assert next_serial(folder, known=(3, 7)) == 8


def test_next_serial_without_a_record_behaves_as_before(tmp_path):
    folder = tmp_path / "output" / "2026-10-23"
    folder.mkdir(parents=True)
    (folder / "fish-002.orig.png").write_bytes(b"x")

    assert next_serial(folder) == 3
    assert next_serial(folder, known=()) == 3


def test_plan_output_skips_numbers_the_record_already_used(tmp_path):
    """账里有 fish-001、图已经清走：素材图不能又叫 fish-001。

    撞出来的同号比"重名"更重：贴纸与带字成图都要按号去借上一条的参数，
    借错一条就把无关生成的种子抄进配方。
    """
    cfg = _cfg(tmp_path)
    outside = tmp_path / "素材" / "IMG-999.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    (cfg.paths.output_dir / TODAY.isoformat()).mkdir(parents=True)

    target, serial = plan_output(
        cfg.paths.output_dir, outside, TODAY, "sticker", known=(1,)
    )

    assert target == cfg.paths.output_dir / TODAY.isoformat() / "fish-002.sticker.png"
    assert serial == 2


def test_plan_output_will_not_take_a_number_from_outside_the_output_folder(tmp_path):
    """外面拿进来一张也叫 fish-007 的图，而账里 007 已经是别人的 —— 换号，不抢。

    沿用号的前提是"这张就是那个号的原图"；从素材目录里捡到同名文件时这个前提
    不成立，跟着它落会顶掉当天真有的那条，去背/加字还会借到别人的种子。
    """
    cfg = _cfg(tmp_path)
    outside = tmp_path / "素材" / "fish-007.orig.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")

    target, serial = plan_output(
        cfg.paths.output_dir, outside, TODAY, "sticker", known=(7,)
    )

    assert serial == 8
    assert target == cfg.paths.output_dir / TODAY.isoformat() / "fish-008.sticker.png"


def test_plan_output_keeps_the_sources_number_even_with_a_record(tmp_path):
    """源图自己就在 output/ 里时沿用它的号 —— 记录里用过的号不该把这一步挤走。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()

    target, serial = plan_output(
        cfg.paths.output_dir, folder / "fish-007.orig.png", TODAY, "sticker", known=(1, 2)
    )

    assert target == folder / "fish-007.sticker.png"
    assert serial == 7


def test_parse_serial_only_reads_fish_names():
    """外来名字（IMG-007.jpg）不算序号 —— 否则它会被当成 7 号去盖 fish-007。"""
    assert parse_serial(Path("fish-007.orig.png")) == 7
    assert parse_serial(Path("IMG-007.jpg")) is None
    assert parse_serial(Path("fish-abc.png")) is None
    assert parse_serial(Path("fish-.png")) is None


def test_daily_dir_uses_iso_date(tmp_path):
    assert daily_dir(tmp_path, date(2026, 1, 2)) == tmp_path / "2026-01-02"


def test_plan_output_follows_the_source_inside_output(tmp_path):
    """源图就在 output/ 里：贴着它落、沿用一个号，只换后缀。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()

    target, serial = plan_output(
        cfg.paths.output_dir, folder / "fish-007.orig.png", TODAY, "captioned"
    )

    assert target == folder / "fish-007.captioned.png"
    assert serial == 7


def test_plan_output_puts_a_foreign_picture_in_todays_folder(tmp_path):
    """素材图（不在 output/ 里）不往那儿写：落到今天的目录，按当天最大号接着排。"""
    cfg = _cfg(tmp_path)
    outside = tmp_path / "素材" / "IMG-001.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    today_dir = cfg.paths.output_dir / TODAY.isoformat()
    today_dir.mkdir(parents=True)
    (today_dir / "fish-004.sticker.png").write_bytes(b"x")

    target, serial = plan_output(cfg.paths.output_dir, outside, TODAY, "sticker")

    assert target == today_dir / "fish-005.sticker.png"
    assert serial == 5

def test_a_foreign_picture_with_a_used_number_gets_a_new_one_even_without_a_record(tmp_path):
    """账被清走（known 空）时只能看目录：那里已经有 fish-007 了，不能落成同号。"""
    cfg = _cfg(tmp_path)
    outside = tmp_path / "素材" / "fish-007.orig.png"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x")
    today_dir = cfg.paths.output_dir / TODAY.isoformat()
    today_dir.mkdir(parents=True)
    (today_dir / "fish-007.orig.png").write_bytes(b"x")

    target, serial = plan_output(cfg.paths.output_dir, outside, TODAY, "sticker")

    assert serial == 8, "目录里那个 fish-007 是别人的，不能顶掉它"
    assert target == today_dir / "fish-008.sticker.png"


def test_a_picture_already_in_output_keeps_its_number_even_if_the_record_knows_it(tmp_path):
    """在 output/ 里的图一律沿用自己那个号 —— 账里记过这个号也一样。

    判据是"它是不是这条链上的一环"，不是"账里有没有这个号"：给 exist 的 fish-007
    去背，账里那条 fish-007 当然记着 7，那不构成"号被占"。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    folder.mkdir(parents=True)
    (folder / "fish-007.orig.png").write_bytes(b"x")

    target, serial = plan_output(
        cfg.paths.output_dir,
        folder / "fish-007.orig.png",
        TODAY,
        "sticker",
        known=(3, 7),
    )

    assert serial == 7
    assert target == folder / "fish-007.sticker.png"


def test_a_symlinked_output_folder_still_finds_the_pictures_own_day(tmp_path):
    """output 是符号链接时也得认出"这张在 output 里" —— 否则跨日去背会落错目录、号还重排。

    `config._resolve` 只拼不 resolve，于是 output_dir 与源图的父目录字面上不相等；
    直接比路径就恒假，昨天的图会掉进今天那个目录。"""
    real = tmp_path / "real-output"
    old = real / "2026-10-22"
    old.mkdir(parents=True)
    (old / "fish-007.orig.png").write_bytes(b"x")
    link = tmp_path / "output"
    link.symlink_to(real, target_is_directory=True)

    target, serial = plan_output(link, old / "fish-007.orig.png", TODAY, "sticker")

    assert target == old / "fish-007.sticker.png"
    assert serial == 7
