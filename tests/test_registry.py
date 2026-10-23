"""两本账：当天的全量记录与仓库里的配方库（票 #11，ADR 0003）。

分成两本的理由是"值不值得进版本控制"：成品图几百 MB、随时能重生成，进 git
只会让仓库膨胀；"什么参数能出什么图"丢了就得重来，所以只把定稿的配方收进
仓库。这个文件管的就是这两次写：一次是出片时顺手记全量，一次是 keep 时挑
出定稿写进配方库。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from lab.registry import (
    Entry,
    library_path,
    load_entries,
    record_path,
    write_entry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 24)


def png_bytes(size: tuple[int, int] = (24, 16)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


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
        negative="",
        seed=12345,
        steps=20,
        cfg=5.0,
        comfyui_version="0.3.40",
    )
    return replace(base, **overrides)


def test_a_record_roundtrips_through_json(tmp_path):
    """条目写出去再读回来要一模一样 —— 记录的唯一用处就是事后照着它重跑。"""
    path = tmp_path / "registry.local.json"
    write_entry(path, entry())

    (back,) = load_entries(path)
    assert back == entry()


def test_the_record_carries_the_engine_version():
    """同种子同参数在不同 ComfyUI 版本下不保证出同一张，所以版本必须记。"""
    payload = entry(comfyui_version="0.3.40").as_json()

    assert payload["comfyuiVersion"] == "0.3.40"


def test_the_record_keeps_the_field_names_design_doc_uses(tmp_path):
    """字段名是 docs/design.md §6.2 定的对外形状：camelCase、预留三个 null。"""
    payload = entry().as_json()

    assert payload["createdAt"] == "2026-10-24T02:05:43+08:00"
    assert payload["src"] is None
    assert payload["preview"] is None
    assert payload["large"] is None
    # 没有来源图的纯文生图，source 是 null 而不是空字典
    assert payload["source"] is None


#: docs/design.md §6.2 那个 jsonc 块里的键，按原顺序抄下来。
#: 少一个、多一个、拼错大小写，都是"文档里写的"和"文件里存的"分了家。
DESIGN_SCHEMA_KEYS = (
    "id",
    "createdAt",
    "kind",
    "file",
    "width",
    "height",
    "animated",
    "workflow",
    "model",
    "prompt",
    "negative",
    "seed",
    "steps",
    "cfg",
    "comfyuiVersion",
    "requestedWidth",
    "requestedHeight",
    "source",
    "caption",  # glossary-ok: 配方条目的台词字段名，§6.2 定死的对外键名
    "src",
    "preview",
    "large",
)


def test_the_entry_writes_exactly_the_keys_the_design_doc_lists():
    """§6.2 是配方库的对外契约，它和 as_json 得逐键对得上（含顺序）。"""
    assert tuple(entry().as_json()) == DESIGN_SCHEMA_KEYS


def test_the_design_doc_in_repo_still_lists_the_same_keys():
    """文档里那一段真的还在、真的还是这些键 —— 只对代码自己的常量没意义。"""
    import re

    text = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    start = text.index("### 6.2 配方条目 schema")
    fence = chr(96) * 3
    opening = fence + "jsonc"
    after = text.index(opening, start) + len(opening)
    block = text[after : text.index(fence, after)]
    # 只认顶层的 2 空格缩进；source 的一层子键缩进 4 格，不该混进来。
    found = tuple(
        key
        for line in block.splitlines()
        if line.startswith('  "')
        for key in re.findall(r'"([A-Za-z]+)":', line)
    )

    assert found == DESIGN_SCHEMA_KEYS


def test_the_record_file_lives_beside_the_pictures_of_that_day(tmp_path):
    """全量记录跟当天的图放一起：output/ 整个目录不进 git，它自然也就没进去。"""
    assert record_path(tmp_path / "output", TODAY) == (
        tmp_path / "output" / "2026-10-24" / "registry.local.json"
    )


def test_writing_a_second_entry_appends_instead_of_replacing(tmp_path):
    path = tmp_path / "registry.local.json"
    write_entry(path, entry())
    write_entry(path, entry(id="fish-002", file="output/2026-10-24/fish-002.orig.png"))

    assert [one.id for one in load_entries(path)] == ["fish-001", "fish-002"]


def test_the_same_id_and_kind_is_written_once(tmp_path):
    """同一张图重做一遍（同 id 同 kind）应当覆盖，不是堆两条互相打架的记录。"""
    path = tmp_path / "registry.local.json"
    write_entry(path, entry(seed=1))
    write_entry(path, entry(seed=2))

    (back,) = load_entries(path)
    assert back.seed == 2


def test_the_two_kinds_of_the_same_id_coexist(tmp_path):
    """fish-001 的原图与贴纸是同一个号的两个面，两条都要在。"""
    path = tmp_path / "registry.local.json"
    write_entry(path, entry())
    write_entry(path, entry(kind="sticker", file="output/2026-10-24/fish-001.sticker.png"))

    assert [one.kind for one in load_entries(path)] == ["orig", "sticker"]


def test_the_same_id_on_two_days_does_not_overwrite_yesterdays(tmp_path):
    """号只在当天之内唯一，两本账却长期累积 —— 去重不能只看（号，阶段）。

    实测过的场景：昨天留过 fish-001 的带字成图，今天又从 001 起步，两条撞在
    一起时早期实现直接盖掉前一条，配方库悄悄少一条，谁也不知道。
    """
    path = library_path(tmp_path)
    write_entry(path, entry(kind="captioned", seed=111, created_at="2026-10-23T10:00:00+08:00"), library=True)
    write_entry(path, entry(kind="captioned", seed=222, created_at="2026-10-24T10:00:00+08:00"), library=True)

    assert [one.seed for one in load_entries(path)] == [111, 222]


def test_an_empty_record_file_reads_as_nothing(tmp_path):
    assert load_entries(tmp_path / "nope.json") == []


def test_a_broken_record_file_says_which_file(tmp_path):
    path = tmp_path / "registry.local.json"
    path.write_text("{不是 JSON", encoding="utf-8")

    try:
        load_entries(path)
    except ValueError as exc:
        assert str(path) in str(exc)
    else:  # pragma: no cover - 没抛就是错的
        raise AssertionError("坏文件应当报错，而不是当成空的")


def test_the_recipe_library_is_the_one_file_that_goes_into_git(tmp_path):
    """配方库固定落在仓库的 data/registry.json —— 版本控制要盯的就是它。"""
    assert library_path(tmp_path) == tmp_path / "data" / "registry.json"


def test_the_two_ledgers_land_on_opposite_sides_of_gitignore():
    """验收判据 4：全量记录不进 git、配方库进 git。

    这是两本账存在的全部理由（ADR 0003），所以拿 .gitignore 本身当断言 ——
    只测路径长什么样守不住它：把 `paths.output_dir` 指到别处，当天的账就跟着进了
    版本库，而代码一行没变。
    """
    ignored = [
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert "output/" in ignored, "output/ 整个目录必须被忽略，当天的账就住在里面"
    assert "data/registry.json" not in ignored
    assert "data/" not in ignored, "整个 data/ 被忽略的话，配方库就进不了 git"


def test_the_library_keeps_its_header_and_updated_stamp(tmp_path):
    """data/registry.json 里那句 _comment 是写给将来打开这个文件的人看的，别写丢。"""
    path = library_path(tmp_path)
    write_entry(path, entry(), library=True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "配方库" in payload["_comment"]
    assert payload["version"] == 1
    assert payload["updatedAt"]
    assert payload["items"][0]["id"] == "fish-001"

def test_a_ledger_with_a_falsy_items_value_is_not_treated_as_empty(tmp_path):
    """items 是 ""/0/false/null 时不能当空账 —— write_entry 是整本读-改-写。

    读成空账的后果不是"少看见几条"：下一条一写回去，这一天记过的条目就被整本
    覆盖掉了，而且没有任何提示。"""
    path = tmp_path / "registry.local.json"
    for junk in ('""', "0", "false", "null"):
        path.write_text(
            '{"version": 1, "updatedAt": "2026-10-24T02:00:00+08:00", "items": ' + junk + "}",
            encoding="utf-8",
        )
        try:
            load_entries(path)
        except ValueError as exc:
            assert str(path) in str(exc)
        else:  # pragma: no cover - 没抛就是错的
            raise AssertionError(f"items={junk} 应当报错，而不是当成空的")


def test_an_entry_with_a_null_field_says_which_field(tmp_path):
    """必填栏是 null 与缺键同罪：`str(None)` 会把 id 洗成字面 'None' 写回账里。"""
    path = tmp_path / "registry.local.json"
    payload = entry().as_json()
    payload["id"] = None
    path.write_text(
        json.dumps({"version": 1, "updatedAt": "x", "items": [payload]}, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        load_entries(path)
    except ValueError as exc:
        assert "id" in str(exc)
    else:  # pragma: no cover - 没抛就是错的
        raise AssertionError("id 是 null 应当报错")

