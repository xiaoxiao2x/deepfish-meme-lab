"""素材库（M5.4）：瀑布流、三个筛选、送去出片台。

这一页有两条性子，测试照着它们写：

- **只读**：跑一趟页面、拿去出片，素材的原文件一个字节都不许变。素材是只读引用
  （design.md §7.2 三条铁律的头两条），「我们没写它」这句话得能自己证明。
- **照索引认**：素材不在仓库里，页面能显示、能当底图，靠的是「索引里确实记着
  这一张」，不是"你给个路径我就读"。索引之外的东西一律当不存在。

真起一遍服务再请求，理由同 tests/test_web.py：判据是"点得动"，比路由表证明不了。
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lab import pool
from lab.assets import build_index, save_index
from lab.config import load_config
from lab.recording import repo_relative
from lab.registry import Entry, EntrySource, library_path, write_entry

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------- 造素材、造账 ----------


def picture(path: Path, size: tuple[int, int] = (640, 480), color=(30, 90, 160)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def animation(path: Path, size: tuple[int, int] = (64, 64)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = [Image.new("RGB", size, c) for c in ("#ff0000", "#0000ff")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


def _cfg(tmp_path: Path):
    cfg = load_config(REPO_ROOT)
    return replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(
            cfg.paths,
            output_dir=tmp_path / "output",
            asset_index=tmp_path / "assets" / "index.local.json",
        ),
    )


def _pooled(tmp_path: Path, *, stills: int = 3, gifs: int = 0):
    """在 tmp_path 下造一个素材池，扫成索引，回配置文件。

    两个根：一个叫「一批」，一个叫「另一批」—— 来源批次那个筛选要的正是两根并存。
    """
    cfg = _cfg(tmp_path)
    first = tmp_path / "pool" / "一批"
    second = tmp_path / "pool" / "另一批"
    for nth in range(stills):
        picture(first / f"still-{nth}.png", color=(10 * nth, 40, 90))
    for nth in range(gifs):
        animation(second / f"loop-{nth}.gif")
    index = build_index([first, second])
    save_index(index, cfg.paths.asset_index)
    return cfg, index


def _entry(**overrides) -> Entry:
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


# ---------- 索引这一层 ----------


def test_the_pool_comes_out_of_the_index(tmp_path):
    """瀑布流里的格子就是从索引的 assets 里来的，一条不多一条不少。"""
    cfg, index = _pooled(tmp_path, stills=3, gifs=2)

    tiles = pool.tiles(cfg, index)

    assert len(tiles) == 5
    assert {tile.name for tile in tiles} == {
        "still-0.png",
        "still-1.png",
        "still-2.png",
        "loop-0.gif",
        "loop-1.gif",
    }


def test_a_missing_index_says_how_to_make_one(tmp_path):
    """索引不在不是白屏：说清楚该跑哪条命令。"""
    cfg = _cfg(tmp_path)

    index, message = pool.open_index(cfg)

    assert index is None
    assert "lab assets scan" in message


def test_a_broken_index_is_a_message_not_a_crash(tmp_path):
    """索引坏了（手工改错、写到一半断电）同样只是一句话。"""
    cfg = _cfg(tmp_path)
    cfg.paths.asset_index.parent.mkdir(parents=True, exist_ok=True)
    cfg.paths.asset_index.write_text("{ 这不是 JSON", encoding="utf-8")

    index, message = pool.open_index(cfg)

    assert index is None
    assert "lab assets scan" in message


def test_the_index_is_handed_over_whole_when_it_is_there(tmp_path):
    cfg, index = _pooled(tmp_path, stills=2)

    opened, message = pool.open_index(cfg)

    assert opened is not None
    assert opened.count == index.count
    assert message == ""


def test_batches_are_the_roots_the_index_was_built_from(tmp_path):
    """来源批次就是扫描时的那几个根，各自数一下有多少张。"""
    cfg, index = _pooled(tmp_path, stills=3, gifs=2)

    batches = pool.batches(pool.tiles(cfg, index))

    assert [(batch.name, batch.count) for batch in batches] == [
        ("一批", 3),
        ("另一批", 2),
    ]


def test_the_two_kind_filters_split_stills_from_animations(tmp_path):
    cfg, index = _pooled(tmp_path, stills=3, gifs=2)
    tiles = pool.tiles(cfg, index)

    still = pool.sift(tiles, kind="still")
    animated = pool.sift(tiles, kind="animated")

    assert len(still) == 3
    assert len(animated) == 2
    assert all(tile.kind == "still" for tile in still)
    assert all(tile.kind == "animated" for tile in animated)


def test_a_batch_filter_keeps_only_that_root(tmp_path):
    cfg, index = _pooled(tmp_path, stills=3, gifs=2)
    tiles = pool.tiles(cfg, index)
    animated = pool.sift(tiles, kind="animated")

    kept = pool.sift(tiles, batch=animated[0].asset.root.as_posix())

    assert [tile.name for tile in kept] == ["loop-0.gif", "loop-1.gif"]


def test_unused_only_drops_what_the_ledger_remembers(tmp_path):
    """「用过」的判据只有一处：账里有没有一条 source 指着这张素材。

    送去出片台不复制素材，出片时直接把原文件交给引擎 —— 于是全量记录里
    source.image 记的就是素材自己的路径。配方库与当天的全量记录都算数。
    """
    cfg, index = _pooled(tmp_path, stills=3)
    tiles = pool.tiles(cfg, index)
    spent = tiles[1]

    write_entry(
        library_path(cfg.repo_root),
        _entry(
            source=EntrySource(
                type="img2img", image=repo_relative(cfg, spent.asset.path), denoise=0.45
            )
        ),
        library=True,
    )

    kept = pool.sift(pool.tiles(cfg, index), unused=True)

    assert [tile.name for tile in kept] == ["still-0.png", "still-2.png"]
    assert [tile.name for tile in pool.tiles(cfg, index) if tile.used] == ["still-1.png"]


def test_a_todays_record_counts_as_used_too(tmp_path):
    """只活当天的那本也算 —— 出过片没保留的，同样不该再算「没用过」。"""
    from datetime import date

    from lab.registry import record_path

    cfg, index = _pooled(tmp_path, stills=2)
    spent = pool.tiles(cfg, index)[0]
    write_entry(
        record_path(cfg.paths.output_dir, date(2026, 10, 24)),
        _entry(
            source=EntrySource(
                type="img2img", image=repo_relative(cfg, spent.asset.path), denoise=0.45
            )
        ),
    )

    assert [tile.name for tile in pool.tiles(cfg, index) if tile.used] == ["still-0.png"]


def test_finding_a_tile_by_its_key(tmp_path):
    cfg, index = _pooled(tmp_path, stills=2)
    wanted = pool.tiles(cfg, index)[0]

    found = pool.find_tile(cfg, index, wanted.key)

    assert found.name == wanted.name
    assert found.asset.path == wanted.asset.path


def test_a_key_the_index_does_not_know_is_refused(tmp_path):
    """索引里没有这一串 —— 不是一个可以拿来读任意文件的入口。"""
    cfg, index = _pooled(tmp_path, stills=2)

    with pytest.raises(ValueError) as caught:
        pool.find_tile(cfg, index, "0" * 40)

    assert "索引" in str(caught.value)


# ---------- 缩略图 ----------


def test_a_thumbnail_is_capped_at_the_thumbs_width(tmp_path):
    cfg, index = _pooled(tmp_path, stills=1)
    big = pool.tiles(cfg, index)[0]

    with Image.open(BytesIO(pool.thumb_bytes(big.asset.path))) as thumb:
        assert thumb.width == pool.THUMB_WIDTH
        assert thumb.height == round(pool.THUMB_WIDTH * 480 / 640)


def test_a_small_picture_is_not_blown_up(tmp_path):
    """比瀑布流那档还小的图保持原样：放大只是把模糊摊开。"""
    cfg = _cfg(tmp_path)
    root = tmp_path / "pool" / "小图"
    picture(root / "tiny.png", size=(120, 90))
    index = build_index([root])
    save_index(index, cfg.paths.asset_index)
    tile = pool.tiles(cfg, index)[0]

    with Image.open(BytesIO(pool.thumb_bytes(tile.asset.path))) as thumb:
        assert (thumb.width, thumb.height) == (120, 90)


def test_a_picture_that_cannot_be_read_says_which_one(tmp_path):
    _cfg(tmp_path)
    root = tmp_path / "pool" / "坏的"
    root.mkdir(parents=True)
    broken = root / "broken.png"
    broken.write_bytes(b"not a png")

    with pytest.raises(ValueError) as caught:
        pool.thumb_bytes(broken)

    assert "broken.png" in str(caught.value)
# ---------- 这一页本身 ----------


def _http(cfg):
    """真起一遍服务：判据是「点得动」，路由表证明不了。"""
    from contextlib import contextmanager

    from lab.web import create_app
    from tests.test_web_api import _serving

    @contextmanager
    def run():
        with _serving(create_app(cfg)) as base:
            yield base

    return run()


#: 拼进 URL 之前要保留的字符：查询串的骨架 + 路径分隔符。其余（中文目录名之类）
#: 一律转义 —— urllib 只认 ASCII，把中文原样塞进去连请求都发不出去。
_URL_SAFE = "/?=&:;+,%[]@!$'()*"


def _url(base: str, path: str) -> str:
    """浏览器会自动转义，够不着浏览器的测试得自己来。"""
    return base + urllib.parse.quote(path, safe=_URL_SAFE)


def _page(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(_url(base, path), timeout=15) as answer:
            return answer.status, answer.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _raw(base: str, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(_url(base, path), timeout=15) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_the_wall_shows_every_tile_in_the_index(tmp_path):
    """415 张要能一张不落地滚到 —— 这里用小规模证明「一条不少」。"""
    cfg, index = _pooled(tmp_path, stills=3, gifs=2)

    with _http(cfg) as base:
        status, html = _page(base, "/assets")

    assert status == 200
    for tile in pool.tiles(cfg, index):
        assert tile.name in html
        assert 'data-key="' + tile.key + '"' in html


def test_every_picture_in_the_wall_is_loaded_lazily(tmp_path):
    """瀑布流一次几百张：不到眼前不取图，loading="lazy" 是这条判据的落点。"""
    cfg, index = _pooled(tmp_path, stills=2)

    with _http(cfg) as base:
        _status, html = _page(base, "/assets")

    thumbs = [line for line in html.splitlines() if "/asset/thumb/" in line]
    assert len(thumbs) == len(pool.tiles(cfg, index))
    assert all('loading="lazy"' in line for line in thumbs)


def test_a_missing_index_tells_you_what_to_run(tmp_path):
    """索引不在不是白屏：页面上要有那句「先跑 lab assets scan」。"""
    cfg = _cfg(tmp_path)

    with _http(cfg) as base:
        status, html = _page(base, "/assets")

    assert status == 200
    assert "lab assets scan" in html


def test_the_batch_filter_keeps_one_source(tmp_path):
    cfg, _index = _pooled(tmp_path, stills=3, gifs=2)
    second = (tmp_path / "pool" / "另一批").as_posix()

    with _http(cfg) as base:
        _status, html = _page(base, "/assets?batch=" + second)

    assert "loop-0.gif" in html
    assert "still-0.png" not in html


def test_the_kind_filter_keeps_animations_apart(tmp_path):
    cfg, _index = _pooled(tmp_path, stills=3, gifs=2)

    with _http(cfg) as base:
        _status, still = _page(base, "/assets?kind=still")
        _status, animated = _page(base, "/assets?kind=animated")

    assert "still-0.png" in still and "loop-0.gif" not in still
    assert "loop-0.gif" in animated and "still-0.png" not in animated


def test_the_unused_filter_drops_what_the_ledger_remembers(tmp_path):
    cfg, index = _pooled(tmp_path, stills=3)
    spent = pool.tiles(cfg, index)[1]
    write_entry(
        library_path(cfg.repo_root),
        _entry(
            source=EntrySource(
                type="img2img", image=repo_relative(cfg, spent.asset.path), denoise=0.45
            )
        ),
        library=True,
    )

    with _http(cfg) as base:
        _status, html = _page(base, "/assets?unused=1")

    assert spent.name not in html
    assert "still-0.png" in html


def test_the_filters_remember_what_was_chosen(tmp_path):
    """选完筛一下，控件上还看得见刚才选的是哪一档。"""
    cfg, _index = _pooled(tmp_path, stills=3, gifs=2)

    with _http(cfg) as base:
        _status, html = _page(base, "/assets?kind=animated&unused=1")

    assert 'value="animated" selected' in html
    assert "checked" in html


def test_a_thumbnail_comes_out_through_the_wall_route(tmp_path):
    cfg, index = _pooled(tmp_path, stills=1)
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        status, raw = _raw(base, "/asset/thumb/" + tile.key + ".png")

    assert status == 200
    with Image.open(BytesIO(raw)) as thumb:
        assert thumb.format == "PNG"
        assert thumb.width == pool.THUMB_WIDTH


def test_a_key_the_index_does_not_know_is_a_404(tmp_path):
    """不是任意文件读取入口：索引外的哈希一律不认。"""
    cfg, _index = _pooled(tmp_path, stills=1)

    with _http(cfg) as base:
        status, _body = _raw(base, "/asset/thumb/" + "0" * 40 + ".png")

    assert status == 404


def test_the_full_picture_comes_out_for_the_big_view(tmp_path):
    """大图页要看原图 —— 还是照索引认，缩略图之外再给一条原图的路。"""
    cfg, index = _pooled(tmp_path, stills=1)
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        status, raw = _raw(base, "/asset/" + tile.key + "/" + tile.name)

    assert status == 200
    assert raw == tile.asset.path.read_bytes()


def test_a_tile_says_how_to_send_it_to_the_bench(tmp_path):
    """每张都有「送去出片台」，链接里带的是索引里的那个哈希。"""
    cfg, index = _pooled(tmp_path, stills=1)
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        _status, html = _page(base, "/assets")

    assert 'href="/?from-asset=' + tile.key + '"' in html


def test_sending_a_tile_lands_it_in_the_base_slot(tmp_path):
    """点过去，出片台的底图区里就是这一张，预览走的是同一个哈希。"""
    cfg, index = _pooled(tmp_path, stills=1)
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        status, html = _page(base, "/?from-asset=" + tile.key)

    assert status == 200
    assert tile.name in html
    assert 'data-base="asset:' + tile.key + '"' in html


def test_sending_something_the_index_does_not_know_is_refused_politely(tmp_path):
    cfg, _index = _pooled(tmp_path, stills=1)

    with _http(cfg) as base:
        status, html = _page(base, "/?from-asset=" + "0" * 40)

    assert status == 200
    assert "索引" in html


def test_the_big_view_opens_that_one_picture(tmp_path):
    cfg, index = _pooled(tmp_path, stills=1)
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        status, html = _page(base, "/assets?open=" + tile.key)

    assert status == 200
    assert "/asset/" + tile.key + "/" + tile.name in html


def test_reading_the_page_does_not_touch_the_pool(tmp_path):
    """只读这条承诺得能自己证明：跑一趟页面，素材的字节与 mtime 一个都不变。"""
    cfg, index = _pooled(tmp_path, stills=2, gifs=1)
    before = {
        one.path: (one.path.read_bytes(), one.path.stat().st_mtime_ns) for one in index.assets
    }
    tile = pool.tiles(cfg, index)[0]

    with _http(cfg) as base:
        _page(base, "/assets")
        _page(base, "/assets?unused=1")
        _raw(base, "/asset/thumb/" + tile.key + ".png")
        _raw(base, "/asset/" + tile.key + "/" + tile.name)

    after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before}
    assert after == before

# ---------- 送去出片台之后：真能拿它出片 ----------


def _bench(cfg, engine):
    """带引擎起一遍服务：判据是「这张素材真能当底图」，光有页面证明不了。"""
    from contextlib import contextmanager

    from lab.web import create_app
    from tests.test_web_api import _serving

    # 真出片要读工作流模板：配置把仓库根挪到了 tmp_path，得先把模板搬过去。
    shutil.copytree(REPO_ROOT / "workflows", cfg.repo_root / "workflows", dirs_exist_ok=True)

    @contextmanager
    def run():
        with _serving(create_app(cfg, client=engine)) as base:
            yield base

    return run()


def test_a_tile_sent_to_the_bench_can_actually_be_the_base_image(tmp_path):
    """「送去出片台」填的是 asset:<sha1> —— 出片时服务器自己把它落进 output。

    素材不在 output 里，/images 读不到它，引擎的 LoadImage 更读不到；所以那一串
    只是「待落地的地址」，真出片时得先有一份在 output 里的副本。素材本身仍然只读。
    """
    from tests.test_web_api import TickingEngine, _events, _post

    engine = TickingEngine()
    cfg, index = _pooled(tmp_path, stills=2)
    tile = pool.tiles(cfg, index)[0]
    original = (tile.asset.path.read_bytes(), tile.asset.path.stat().st_mtime_ns)

    with _bench(cfg, engine) as base:
        status, body = _post(
            base + "/api/make",
            {
                "prompt": "same fish, another mood",
                "count": 1,
                "init_image": "asset:" + tile.key,
                "denoise": 0.4,
            },
        )
        assert status == 200, body
        _events(base + f"/api/jobs/{body['job']}/events", stop="finished")

    assert status == 200, body
    assert engine.uploads, "底图得先交给引擎"
    handed = engine.uploads[0]
    assert handed.is_relative_to(cfg.paths.output_dir.resolve()), "引擎只认 output 里的底图"
    assert handed.read_bytes() == tile.asset.path.read_bytes(), "交给引擎的得是这张素材本身"
    assert (tile.asset.path.read_bytes(), tile.asset.path.stat().st_mtime_ns) == original, (
        "素材是引用，出片不许改它"
    )


def test_the_same_tile_twice_does_not_pile_up_copies(tmp_path):
    """同一张素材连着出两批：output 里只落一份，别每批复制一遍。"""
    from tests.test_web_api import TickingEngine, _events, _post

    engine = TickingEngine()
    cfg, index = _pooled(tmp_path, stills=1)
    key = pool.tiles(cfg, index)[0].key

    with _bench(cfg, engine) as base:
        for prompt in ("first", "second"):
            status, body = _post(
                base + "/api/make",
                {"prompt": prompt, "count": 1, "init_image": "asset:" + key},
            )
            assert status == 200, body
            _events(base + f"/api/jobs/{body['job']}/events", stop="finished")

    landed = [one.name for one in cfg.paths.output_dir.rglob("*.png")]
    assert len([name for name in landed if name.startswith("asset-")]) == 1, landed


def test_a_key_the_index_does_not_know_is_refused_at_the_bench(tmp_path):
    """asset: 后面跟着一串索引里没有的东西：照索引认，不替你找文件。"""
    from tests.test_web_api import TickingEngine, _post

    engine = TickingEngine()
    cfg, _index = _pooled(tmp_path, stills=1)

    with _bench(cfg, engine) as base:
        status, body = _post(
            base + "/api/make",
            {"prompt": "x", "count": 1, "init_image": "asset:" + "0" * 40},
        )

    assert status == 400
    assert "索引" in body["message"]
    assert engine.uploads == []

def test_a_tile_that_was_made_into_a_picture_is_marked_used(tmp_path):
    """真走一遍出片：账上记的是 output 里那份副本，不是素材的原路径。

    「只看没用过的」要认得出这一张 —— 素材原件不在 output 里，账上永远不会出现
    它的路径，能对上号的只有副本名里那截 sha1。所以这条不能手造账：那种
    「把素材路径直接写进 source.image」的形状，真实流程根本产生不了。
    """
    from tests.test_web_api import TickingEngine, _events, _post

    engine = TickingEngine()
    cfg, index = _pooled(tmp_path, stills=2)
    used, clean = pool.tiles(cfg, index)[:2]

    with _bench(cfg, engine) as base:
        status, body = _post(
            base + "/api/make",
            {"prompt": "x", "count": 1, "init_image": "asset:" + used.key},
        )
        assert status == 200, body
        _events(base + f"/api/jobs/{body['job']}/events", stop="finished")

        reopened, problem = pool.open_index(cfg)
        assert reopened is not None, problem
        after = pool.tiles(cfg, reopened)
        assert [tile.used for tile in after] == [True, False], "出过片的那张才算用过"

        _, html = _page(base, "/assets?unused=1")
        assert used.name not in html, "用过的不该再出现在「只看没用过的」里"
        assert clean.name in html
