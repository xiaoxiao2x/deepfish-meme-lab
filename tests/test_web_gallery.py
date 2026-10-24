"""成品库（M5.3）：缩略图、按 seed 搜、用同参数重跑、查看登记项。

判据来自 issue #18：配方库每条都有缩略图与配方摘要；按 seed 搜得到旧的那条；
「用同参数重跑」把参数带回出片台；「查看登记项」看得到当天账里那一条的完整字段。

缩略图这一半单独测：它是这一票唯一新长出来的取图路径，与页面怎么摆无关。
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lab import showcase

REPO_ROOT = Path(__file__).resolve().parents[1]


def picture(path: Path, size: tuple[int, int] = (1024, 1024), mode: str = "RGB") -> Path:
    """在 path 上画一张真图：尺寸可指定，默认按出片那档（1024）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "RGBA":
        Image.new("RGBA", size, (0, 0, 0, 0)).save(path, format="PNG")
    else:
        Image.new("RGB", size, (10, 20, 30)).save(path, format="PNG")
    return path


def test_a_thumbnail_is_no_wider_than_a_card_needs(tmp_path):
    """1024 的成品缩到卡片那一档：宽 480（240 CSS 像素 × 2 倍屏）。"""
    big = picture(tmp_path / "fish-001.orig.png")

    with Image.open(BytesIO(showcase.thumb_bytes(big))) as thumb:
        assert thumb.width == showcase.THUMB_WIDTH
        assert thumb.height == showcase.THUMB_WIDTH


def test_a_thumbnail_keeps_transparency(tmp_path):
    """贴纸是带透明区的：缩完还得是透明，不能垫成白底。"""
    sticker = picture(tmp_path / "fish-001.sticker.png", (1024, 512), mode="RGBA")

    with Image.open(BytesIO(showcase.thumb_bytes(sticker))) as thumb:
        assert thumb.mode in ("RGBA", "LA", "PA")
        assert thumb.width == showcase.THUMB_WIDTH
        assert thumb.height == showcase.THUMB_WIDTH // 2


def test_a_small_picture_is_not_blown_up(tmp_path):
    """比卡片还小的图不放大 —— 放大只是把模糊摊开。"""
    small = picture(tmp_path / "fish-002.orig.png", (120, 90))

    with Image.open(BytesIO(showcase.thumb_bytes(small))) as thumb:
        assert (thumb.width, thumb.height) == (120, 90)


def test_a_file_that_is_not_a_picture_says_so(tmp_path):
    """原图被删了、或者根本不是图：报一句人话，别把半截字节当图片发出去。"""
    broken = tmp_path / "fish-003.orig.png"
    broken.write_text("这不是 PNG", encoding="utf-8")

    with pytest.raises(ValueError, match="缩不出来"):
        showcase.thumb_bytes(broken)

# ---------- 配方库摊成页面要的样子：分组、搜索 ----------


def _cfg(tmp_path: Path):
    from dataclasses import replace

    from lab.config import load_config

    cfg = load_config(REPO_ROOT)
    return replace(
        cfg, repo_root=tmp_path, paths=replace(cfg.paths, output_dir=tmp_path / "output")
    )


def _entry(**overrides):
    from dataclasses import replace

    from lab.registry import Entry

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
        negative="",
        seed=996012593,
        steps=50,
        cfg=3.5,
        comfyui_version="0.42.0",
    )
    return replace(base, **overrides)


def _kept(cfg, **overrides):
    from lab.registry import library_path, write_entry

    entry = _entry(**overrides)
    write_entry(library_path(cfg.repo_root), entry, library=True)
    return entry


def test_the_gallery_groups_by_day_newest_first(tmp_path):
    """按日期分组：最近留的那天排在最前面，同一天归一组。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(
        cfg,
        id="fish-002",
        kind="sticker",
        created_at="2026-10-22T09:00:00+08:00",
        file="output/2026-10-22/fish-002.sticker.png",
    )
    _kept(cfg, id="fish-003", created_at="2026-10-24T08:00:00+08:00",
          file="output/2026-10-24/fish-003.orig.png")

    groups = showcase.groups(showcase.cards(cfg))

    assert [group.day for group in groups] == ["2026-10-24", "2026-10-22"]
    assert [card.entry.id for card in groups[0].cards] == ["fish-003", "fish-001"]


def test_a_seed_finds_that_one_old_picture(tmp_path):
    """流 C 的第一步：按 seed 搜，就搜得到一周前那条。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001", seed=996012593)
    _kept(cfg, id="fish-002", seed=12345, file="output/2026-10-24/fish-002.orig.png")

    found = showcase.search(showcase.cards(cfg), "996012593")

    assert [card.entry.id for card in found] == ["fish-001"]


def test_a_word_finds_it_in_the_prompt_or_the_model(tmp_path):
    """关键词在台词里、在模型名里都算命中，且不挑大小写。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(cfg, id="fish-002", kind="sticker", model="Lucida", prompt="一条睡觉的胖鱼",
          file="output/2026-10-24/fish-002.sticker.png")

    assert [card.entry.id for card in showcase.search(showcase.cards(cfg), "whale")] == [
        "fish-001"
    ]
    assert [card.entry.id for card in showcase.search(showcase.cards(cfg), "lucida")] == [
        "fish-002"
    ]


def test_an_empty_search_shows_everything(tmp_path):
    """搜索框空着就是不过滤 —— 成品库默认摊全部。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(cfg, id="fish-002", file="output/2026-10-24/fish-002.orig.png")

    assert len(showcase.search(showcase.cards(cfg), "  ")) == 2


def test_one_card_is_looked_up_by_its_own_identity(tmp_path):
    """缩略图端点按（日期, 号, 阶段）找回账里那一条：号码重复也不串。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(cfg, id="fish-001", kind="sticker", model="Lucida",
          file="output/2026-10-24/fish-001.sticker.png")

    picked = showcase.find_card(cfg, "2026-10-24", "fish-001", "sticker")

    assert picked.entry.model == "Lucida"


def test_a_card_that_is_not_there_is_refused(tmp_path):
    """账里没有这一条：报出来，别拿别的号顶上。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")

    with pytest.raises(ValueError, match="成品库里没有"):
        showcase.find_card(cfg, "2026-10-24", "fish-999", "orig")

def test_a_card_says_whether_its_picture_is_still_there(tmp_path):
    """图在不在是账与盘上的事实：在给路径、不在给 None。

    列表页要一次问 N 张，一张不在不该让整页问不下去，所以这里不抛异常。
    """
    cfg = _cfg(tmp_path)
    entry = _kept(cfg, id="fish-001")
    there = picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")
    card = showcase.find_card(cfg, "2026-10-24", entry.id, entry.kind)

    assert showcase.picture_path(cfg, card) == there

    there.unlink()

    assert showcase.picture_path(cfg, card) is None


# ---------- 页面：/gallery 摊出配方库 ----------


def _http(cfg, *, opener=None):
    """真起一次服务 —— 判据要的是"页面上看得见"，只比路由表证明不了。"""
    from lab.web import create_app
    from tests.test_web_api import _serving

    return _serving(create_app(cfg, opener=opener))


def _page(base: str, path: str) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(base + path, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _get_bytes(base: str, path: str) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(base + path, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_the_gallery_page_lists_what_the_recipe_book_holds(tmp_path):
    """判据 ①：配方库里每条都在页面上，带缩略图与配方摘要（模型 / 种子 / 步数）。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")

    with _http(cfg) as base:
        status, html = _page(base, "/gallery")

    assert status == 200
    assert "fish-001" in html
    assert "flux-2-klein-base-4b-fp8.safetensors" in html
    assert "996012593" in html
    assert "50 步" in html
    assert "/thumb/2026-10-24/fish-001/orig.png" in html


def test_an_empty_recipe_book_says_so(tmp_path):
    """一条都没留时给一句话，不是一张空白页。"""
    cfg = _cfg(tmp_path)

    with _http(cfg) as base:
        status, html = _page(base, "/gallery")

    assert status == 200
    assert "还一条都没留" in html


def test_the_gallery_groups_are_folded_by_day(tmp_path):
    """按日期分组：两天两条，两天各自的标题都在。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(
        cfg,
        id="fish-002",
        created_at="2026-10-22T09:00:00+08:00",
        file="output/2026-10-22/fish-002.orig.png",
    )
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")
    picture(tmp_path / "output" / "2026-10-22" / "fish-002.orig.png")

    with _http(cfg) as base:
        status, html = _page(base, "/gallery")

    assert status == 200
    assert html.index("2026-10-24") < html.index("2026-10-22")


def test_the_search_box_filters_the_page(tmp_path):
    """判据 ②：同一个页面上按 seed 搜，只剩那一条。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    _kept(cfg, id="fish-002", seed=12345, prompt="一条睡觉的胖鱼",
          file="output/2026-10-24/fish-002.orig.png")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")
    picture(tmp_path / "output" / "2026-10-24" / "fish-002.orig.png")

    with _http(cfg) as base:
        status, html = _page(base, "/gallery?q=996012593")

    assert status == 200
    assert "fish-001" in html
    assert "fish-002" not in html


def test_the_search_box_keeps_what_was_typed(tmp_path):
    """筛过之后输入框里还留着那个词 —— 否则不知道自己筛了什么。"""
    cfg = _cfg(tmp_path)

    with _http(cfg) as base:
        status, html = _page(base, "/gallery?q=whale")

    assert status == 200
    assert 'value="whale"' in html


# ---------- 缩略图端点 ----------


def test_a_thumbnail_comes_out_of_the_recipe_book(tmp_path):
    """缩略图是现取现缩的：请求一次，拿到一张真 PNG，宽度是卡片那档。"""
    from io import BytesIO

    from PIL import Image

    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")

    with _http(cfg) as base:
        status, raw = _get_bytes(base, "/thumb/2026-10-24/fish-001/orig.png")

    assert status == 200, raw[:200]
    with Image.open(BytesIO(raw)) as thumb:
        assert thumb.width == showcase.THUMB_WIDTH
        assert thumb.format == "PNG"


def test_a_thumbnail_for_a_card_that_is_not_there_is_a_404(tmp_path):
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")

    with _http(cfg) as base:
        status, _raw = _get_bytes(base, "/thumb/2026-10-24/fish-999/orig.png")

    assert status == 404


def test_a_recipe_whose_picture_was_cleared_says_so(tmp_path):
    """账里那一条还在、图被清走了：404，不是 500 —— 成品库照旧摊得出来。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")

    with _http(cfg) as base:
        status, _raw = _get_bytes(base, "/thumb/2026-10-24/fish-001/orig.png")
        page_status, html = _page(base, "/gallery")

    assert status == 404
    assert page_status == 200, "图不在了不代表这一页打不开"
    assert "fish-001" in html


# ---------- 判据 ③：用同参数重跑 ----------


def test_rerun_goes_back_to_the_page_that_makes_pictures_with_the_recipe_filled_in(tmp_path):
    """判据 ③：点「用同参数重跑」回到出片台，台词 / 种子都填好了。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")

    with _http(cfg) as base:
        status, html = _page(base, "/?from=2026-10-24/fish-001/orig.png")

    assert status == 200
    assert 'value="996012593"' in html
    assert "chibi blue whale girl" in html


def test_a_rerun_of_a_picture_made_from_a_base_image_brings_the_base_too(tmp_path):
    """底图复用出来的那张：重跑得带着同一张底图与当时的重绘强度。"""

    from lab.registry import EntrySource

    cfg = _cfg(tmp_path)
    _kept(
        cfg,
        source=EntrySource(
            type="img2img", image="output/2026-10-24/base-abc.png", denoise=0.55
        ),
    )
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")
    picture(tmp_path / "output" / "2026-10-24" / "base-abc.png")

    with _http(cfg) as base:
        status, html = _page(base, "/?from=2026-10-24/fish-001/orig.png")

    assert status == 200
    assert 'value="0.55"' in html
    assert "base-abc.png" in html


def test_a_rerun_tells_which_model_that_recipe_used(tmp_path):
    """模型在出片台上没有栏可填（§9.3 ①），那就把这条配方当时用的那个交代出来。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")

    with _http(cfg) as base:
        status, html = _page(base, "/?from=2026-10-24/fish-001/orig.png")

    assert status == 200
    assert "flux-2-klein-base-4b-fp8.safetensors" in html
    assert "50 步" in html


def test_the_plain_page_starts_from_nothing(tmp_path):
    """不带 from 就是干净的一页：种子栏空着（留空=随机），别把上一条的号粘过来。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")

    with _http(cfg) as base:
        status, html = _page(base, "/")

    assert status == 200
    assert 'value="996012593"' not in html


def test_rerunning_something_that_is_not_in_the_book_is_refused(tmp_path):
    """from 指的是一条不在账上的：别静默出成空白台，说明白为什么。"""
    cfg = _cfg(tmp_path)

    with _http(cfg) as base:
        status, html = _page(base, "/?from=2026-10-24/fish-999/orig.png")

    assert status == 200
    assert "配方库里没有" in html


# ---------- 判据 ④：查看登记项 ----------


def test_the_record_of_a_card_is_read_from_that_days_ledger(tmp_path):
    """判据 ④：当天全量记录里那一条的完整字段，原样摊出来。"""
    import json
    from dataclasses import replace as _replace
    from datetime import date as _date

    from lab.registry import record_path, write_entry

    cfg = _cfg(tmp_path)
    entry = _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")
    day = _date(2026, 10, 24)
    write_entry(
        record_path(cfg.paths.output_dir, day),
        _replace(entry, workflow="workflows/fish-txt2img.json"),
    )

    with _http(cfg) as base:
        status, raw = _get_bytes(base, "/record/2026-10-24/fish-001/orig.png")

    assert status == 200, raw[:200]
    body = json.loads(raw)
    assert body["id"] == "fish-001"
    assert body["seed"] == 996012593
    assert body["workflow"] == "workflows/fish-txt2img.json"


def test_a_card_whose_ledger_is_gone_says_which_half_is_missing(tmp_path):
    """全量记录被清走了：404 并且说清"配方库那一半还在"。"""
    cfg = _cfg(tmp_path)
    _kept(cfg, id="fish-001")
    picture(tmp_path / "output" / "2026-10-24" / "fish-001.orig.png")

    with _http(cfg) as base:
        status, raw = _get_bytes(base, "/record/2026-10-24/fish-001/orig.png")

    assert status == 404
    assert "配方库" in raw.decode("utf-8")


def test_a_card_that_never_had_a_ledger_says_why(tmp_path):
    """外来的图在配方库里只有它自己：当天那本全量记录本来就没写过它，别拿"被清走了"糊过去。"""
    from dataclasses import replace as _replace

    from lab.registry import library_path, write_entry

    cfg = _cfg(tmp_path)
    imported = _replace(
        _entry(
            id="photo-2024",
            kind="imported",
            file="~/收藏/IMG-2024.jpg",
            workflow="",
            model="",
            seed=0,
            steps=0,
        ),
        cfg=0.0,
    )
    write_entry(library_path(cfg.repo_root), imported, library=True)

    with _http(cfg) as base:
        status, raw = _get_bytes(base, "/record/2026-10-24/photo-2024/imported.png")

    assert status == 404
    assert "本来就没有" in raw.decode("utf-8")


def test_a_card_without_a_recipe_offers_no_rerun(tmp_path):
    """外来的图没有配方可跑：那一栏就别摆一个点了必然报错的链接。"""
    from dataclasses import replace as _replace

    from lab.registry import library_path, write_entry

    cfg = _cfg(tmp_path)
    imported = _replace(
        _entry(id="photo-2024", kind="imported", file="~/收藏/IMG-2024.jpg", workflow="", model=""),
        cfg=0.0,
    )
    write_entry(library_path(cfg.repo_root), imported, library=True)

    with _http(cfg) as base:
        status, html = _page(base, "/gallery")

    assert status == 200
    assert "photo-2024" in html
    assert "用同参数重跑" not in html
    assert "没有配方可重跑" in html

