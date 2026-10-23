"""加字的行为：贴纸上去，带台词成图下来（票 #10）。

分两层测，对应票里的两条判据（5）：

- **快测试**：给一个记录型的画字后端，断言"交给它的话对不对" —— 用哪个字体文件、
  第几个 face、多大字号、什么颜色、描边多宽、每行画在哪个坐标、存成什么格式。
  折行只喂一个假的量字宽函数，不碰真字体；但坐标要拿真字体的 getlength 复算，
  否则"居中"只是口头承诺。
- **慢测试**（`@pytest.mark.slow`）：真字体、真 Pillow，画到盘上再看像素 ——
  白描边真的存在、字色真的是那个色、贴纸的透明区没被涂掉、每个字都落在画布里。

字体的脆弱处单独测：字体不在、字体里没有那个字，都必须在画之前报错，而不是
安安静静画成一个方块（判据 6）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from lab.config import ConfigError, load_config
from lab.lettering import (
    CJK_FONT_CANDIDATES,
    LETTERED_KIND,
    FontChoice,
    InkStyle,
    LetteringError,
    LetteringPlan,
    letter,
    load_font,
    missing_glyphs,
    parse_color,
    plan_lines,
    wrap_text,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 24)
LATIN_ONLY = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def any_cjk_font() -> Path:
    """本机第一个能用的中文字体。一个都没有就跳过这组用例。"""
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    pytest.skip("本机没有中文字体，加字这组用例跳过")


def blank_png(tmp_path: Path, size: tuple[int, int] = (480, 480)) -> Path:
    """一张全透明的画布 —— 慢测试要量的墨迹就全是字本身，不掺别的东西。"""
    from PIL import Image

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "blank.png"
    Image.new("RGBA", size, (0, 0, 0, 0)).save(path, format="PNG")
    return path


def sticker_png(tmp_path: Path, name: str = "fish-007.sticker.png") -> Path:
    """一张真的透明底贴纸：中间一块不透明的实心方块，四周全透明。

    尺寸取 480 —— 和真贴纸一个量级（引擎出 1024，常用 512 上下），
    默认 64px 字号在这上面只占小半屏，正好能看出"压在下沿"是哪样。
    """
    from PIL import Image, ImageDraw

    tmp_path.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (480, 480), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((40, 40, 199, 199), fill=(30, 120, 220, 255))
    path = tmp_path / name
    image.save(path, format="PNG")
    return path


def dark_png(tmp_path: Path, size: tuple[int, int] = (480, 480)) -> Path:
    """一张深色实底图 —— 白描边的用处只有在这种底上才看得见。"""
    from PIL import Image

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "dark.png"
    Image.new("RGBA", size, (16, 16, 16, 255)).save(path, format="PNG")
    return path


def _cfg(tmp_path: Path, font: Path | None = None, **overrides):
    """真配置 + 换掉输出目录，字体默认指到本机真字体上。"""
    cfg = load_config(REPO_ROOT)
    post = replace(
        cfg.postprocess,
        font=font if font is not None else any_cjk_font(),
        **overrides,
    )
    return replace(
        cfg,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
        postprocess=post,
    )


def opened_font(path: Path, size: int, index: int):
    """按排版里记下的选择，把真字体打开 —— 测试自己复算坐标用。"""
    from PIL import ImageFont

    return ImageFont.truetype(str(path), size=size, index=index)


class RecordingPainter:
    """记录型画字后端：不画，只把"被要求画什么"记下来。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, LetteringPlan, Path]] = []

    def paint(self, source: Path, plan: LetteringPlan, target: Path) -> None:
        self.calls.append((source, plan, target))

    @property
    def plan(self) -> LetteringPlan:
        assert self.calls, "画字后端一次都没被调用"
        return self.calls[0][1]


# ---------- 折行（判据 3，只靠假的量字宽函数） ----------


def fixed_width(per_char: float):
    def measure(text: str) -> float:
        return len(text) * per_char

    return measure


def test_wrap_breaks_a_long_line_into_several():
    lines = wrap_text("一二三四五六七八九十", measure=fixed_width(10), limit=40)

    assert lines == ("一二三四", "五六七八", "九十")


def test_wrap_keeps_the_newlines_the_user_typed():
    lines = wrap_text("上句\n下句", measure=fixed_width(10), limit=100)

    assert lines == ("上句", "下句")


def test_wrap_prefers_a_space_for_latin_text():
    """英文在空格处断，别把单词劈成两半。"""
    lines = wrap_text("hello brave new world", measure=fixed_width(10), limit=110)

    assert lines == ("hello brave", "new world")


def test_wrap_keeps_cutting_until_a_long_word_fits():
    """断在空格上之后还剩半截超宽的词：得接着断，不能收下超宽的行。

    这是回归用例 —— 早先版本在空格处断了就撒手，`aaaaaaWWW` 那一行宽过可用宽度，
    描边直接压到画布边上。
    """
    lines = wrap_text("a aaaaaaWWW", measure=fixed_width(10), limit=60)

    assert all(len(line) * 10 <= 60 for line in lines), f"有行超宽：{lines}"
    assert lines == ("a", "aaaaaa", "WWW")


def test_wrap_refuses_a_line_that_can_never_fit():
    with pytest.raises(LetteringError) as caught:
        wrap_text("一", measure=fixed_width(100), limit=10)

    assert "画布太窄" in str(caught.value)


# ---------- 排版（判据 3 与 4 的位置部分，纯计算） ----------


def test_plan_puts_the_block_at_the_bottom_centred():
    style = InkStyle(
        font_size=20,
        fill=(27, 27, 27, 255),
        stroke_width=4,
        stroke_fill=(255, 255, 255, 255),
    )
    # 200 宽，边留 10（半个字高）+ 4（描边），每字 10 → 一行放得下 17 个字
    plan = plan_lines(
        "一二三四五六七八九十" * 2,
        measure=fixed_width(10),
        line_height=20,
        canvas=(200, 200),
        font=FontChoice(path=Path("/fake/font.ttc"), index=2),
        style=style,
    )

    assert [line.text for line in plan.lines] == [
        "一二三四五六七八九十一二三四五六七",
        "八九十",
    ]
    margin = 14  # 半个字高 + 描边
    assert plan.lines[0].origin[0] == round((200 - 170) / 2)  # 居中
    assert plan.lines[1].origin[0] == round((200 - 30) / 2)
    assert plan.lines[0].origin[1] == 200 - margin - 40  # 从底往上堆
    assert plan.lines[1].origin[1] == 200 - margin - 20


def test_plan_refuses_text_too_tall_for_the_canvas():
    style = InkStyle(
        font_size=20,
        fill=(0, 0, 0, 255),
        stroke_width=0,
        stroke_fill=(255, 255, 255, 255),
    )

    with pytest.raises(LetteringError) as caught:
        plan_lines(
            "一" * 200,
            measure=fixed_width(10),
            line_height=20,
            canvas=(100, 100),
            font=FontChoice(path=Path("/fake/font.ttc"), index=2),
            style=style,
        )

    assert "台词太长" in str(caught.value)


# ---------- 交给画字后端的话（判据 5 的快测试） ----------


def test_letter_hands_the_painter_every_decision_it_made(tmp_path):
    """判据 5：字体、字号、颜色、描边、坐标、格式 —— 全在交给后端的排版里。"""
    font = any_cjk_font()
    painter = RecordingPainter()

    letter(
        _cfg(tmp_path, font=font),
        sticker_png(tmp_path),
        "你好呀",
        painter=painter,
        today=TODAY,
    )

    plan = painter.plan
    assert plan.font.path == font, "配了字体就该用配的那个"
    assert plan.text == "你好呀"
    assert plan.style.font_size == 64
    assert plan.style.fill == (27, 27, 27, 255), "字色取 [postprocess].fill"
    assert plan.style.stroke_width == 4
    assert plan.style.stroke_fill == (255, 255, 255, 255), "描边是白的"
    assert plan.image_format == "PNG"
    assert [line.text for line in plan.lines] == ["你好呀"]

    # 坐标不许只看"在画布里"：拿同一个字体、同一个字号复算一遍居中。
    real = opened_font(plan.font.path, plan.style.font_size, plan.font.index)
    ascent, descent = real.getmetrics()
    line_height = ascent + descent + round(plan.style.font_size * 0.15)
    margin = max(1, round(plan.style.font_size * 0.5)) + plan.style.stroke_width
    assert plan.lines[0].origin == (
        round((480 - real.getlength("你好呀")) / 2),
        480 - margin - line_height,
    ), "原点不是照着'居中贴底'算出来的"


def test_letter_stacks_several_lines_without_overlap(tmp_path):
    """多行时每行往下挪一行高，行与行不叠在一起，整块仍然贴底。"""
    painter = RecordingPainter()

    letter(
        _cfg(tmp_path),
        blank_png(tmp_path),
        "这是一句特别长的台词，一行根本放不下",
        painter=painter,
        today=TODAY,
    )

    plan = painter.plan
    assert len(plan.lines) > 1, "这么长的台词没折行"
    real = opened_font(plan.font.path, plan.style.font_size, plan.font.index)
    ascent, descent = real.getmetrics()
    line_height = ascent + descent + round(plan.style.font_size * 0.15)
    margin = max(1, round(plan.style.font_size * 0.5)) + plan.style.stroke_width
    tops = [line.origin[1] for line in plan.lines]
    assert tops == [480 - margin - line_height * len(plan.lines) + line_height * i
                    for i in range(len(plan.lines))]
    for line in plan.lines:
        assert line.origin[0] == round((480 - real.getlength(line.text)) / 2)


def any_cjk_collection() -> Path:
    """本机第一个中文字体合集（.ttc）。一个都没有就跳过这组用例。"""
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.suffix.lower() == ".ttc" and path.is_file():
            return path
    pytest.skip("本机没有中文字体合集（.ttc），挑 face 这组用例跳过")


def test_letter_picks_a_simplified_chinese_face_from_a_collection(tmp_path):
    """合集里第 0 个常常是日文 —— 简体中文必须挑到 SC 那个 face。

    字体显式指定，不靠"本机首选是什么"：那是环境，不是被测的行为。
    """
    painter = RecordingPainter()

    letter(
        _cfg(tmp_path, font=any_cjk_collection()),
        sticker_png(tmp_path),
        "你好呀",
        painter=painter,
        today=TODAY,
    )

    plan = painter.plan
    family = opened_font(plan.font.path, plan.style.font_size, plan.font.index)
    assert family.getname()[0].endswith("SC"), (
        f"挑到的不是简体中文 face：{family.getname()[0]}（index {plan.font.index}）"
    )


def test_a_collection_without_a_chinese_face_is_refused(tmp_path):
    """合集里一个 SC face 都没有时，不许悄悄退回第 0 个去日文里借字形。

    拿本机的合集当样本，只把"脸的名字"换掉 —— 挑 face 的逻辑是真的在跑，
    换成一批没有简体中文的家族名，它就该说话。
    """
    from PIL import ImageFont

    import lab.lettering as lettering

    collection = any_cjk_collection()
    real = ImageFont.truetype

    def renamed(path, size=10, index=0, **kwargs):
        face = real(str(path), size=size, index=index)
        face.getname = lambda: (f"Test Sans {index}", "Regular")  # type: ignore[method-assign]
        return face

    lettering._preferred_index.cache_clear()
    try:
        ImageFont.truetype = renamed  # type: ignore[assignment]
        with pytest.raises(LetteringError) as caught:
            letter(
                _cfg(tmp_path, font=collection),
                sticker_png(tmp_path),
                "你好呀",
                painter=RecordingPainter(),
                today=TODAY,
            )
    finally:
        ImageFont.truetype = real  # type: ignore[assignment]
        lettering._preferred_index.cache_clear()

    assert "简体中文" in str(caught.value), str(caught.value)


def test_letter_takes_the_only_face_of_a_single_file_font(tmp_path):
    """单文件字体只有一个 face —— 挑 face 那一步不能让 index 跑到不存在的号上。"""
    if not LATIN_ONLY.is_file():
        pytest.skip("本机没有 DejaVuSans，单文件字体这组用例跳过")
    painter = RecordingPainter()

    letter(
        _cfg(tmp_path, font=LATIN_ONLY),
        sticker_png(tmp_path),
        "ok",
        painter=painter,
        today=TODAY,
    )

    assert painter.plan.font.index == 0


def test_letter_follows_the_configured_appearance(tmp_path):
    painter = RecordingPainter()

    letter(
        _cfg(
            tmp_path,
            font_size=32,
            stroke_width=2,
            stroke_color="#00FF00",
            fill="#112233",
        ),
        sticker_png(tmp_path),
        "行",
        painter=painter,
        today=TODAY,
    )

    plan = painter.plan
    assert plan.style.font_size == 32
    assert plan.style.stroke_width == 2
    assert plan.style.stroke_fill == (0, 255, 0, 255)
    assert plan.style.fill == (0x11, 0x22, 0x33, 255)


def test_letter_refuses_a_nonsense_stroke_width(tmp_path):
    """负描边不是"小一点"，是配错了 —— 报错，不悄悄夹成 0。"""
    with pytest.raises(ConfigError) as caught:
        letter(
            _cfg(tmp_path, stroke_width=-2),
            sticker_png(tmp_path),
            "行",
            painter=RecordingPainter(),
            today=TODAY,
        )

    assert "描边宽度" in str(caught.value)


def test_letter_names_the_output_like_the_sticker(tmp_path):
    """判据 1：带字成图叫 .captioned.png，落在贴纸旁边、用同一个序号。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    source = sticker_png(folder, name="fish-007.sticker.png")
    painter = RecordingPainter()

    letter(cfg, source, "你好呀", painter=painter, today=TODAY)

    target = painter.calls[0][2]
    assert target == folder / f"fish-007.{LETTERED_KIND}.png", "没贴着贴纸落、或号变了"


# ---------- 全量记录（票 #11） ----------


def records(tmp_path: Path):
    from lab.registry import load_entries, record_path

    return load_entries(record_path(tmp_path / "output", TODAY))


def test_the_lettering_lands_in_the_record(tmp_path):
    """验收判据 1：加字出来的这张也要能在当天账里查到，带着那句台词。"""
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    source = sticker_png(folder, name="fish-007.sticker.png")

    letter(cfg, source, "你好呀", painter=RecordingPainter(), today=TODAY)

    (only,) = records(tmp_path)
    assert only.id == "fish-007"
    assert only.kind == LETTERED_KIND
    assert only.file.endswith(f"fish-007.{LETTERED_KIND}.png")
    assert only.caption == "你好呀"  # glossary-ok: 字段名照 docs/design.md §6.2
    assert (only.width, only.height) == (480, 480)


def _generated_record(tmp_path: Path, kind: str = "orig"):
    """当天的账里放一条同号的"生成来路"，参数是真的，好认。"""
    from lab.registry import Entry, record_path, write_entry

    cfg = _cfg(tmp_path)
    write_entry(
        record_path(cfg.paths.output_dir, TODAY),
        Entry(
            id="fish-007",
            created_at="2026-10-24T02:00:00+08:00",
            kind=kind,
            file=f"output/2026-10-24/fish-007.{kind}.png",
            width=480,
            height=480,
            workflow="workflows/fish-txt2img.json",
            model="flux-2-klein-base-4b-fp8.safetensors",
            prompt="chibi blue whale girl",
            negative="",
            seed=12345,
            steps=20,
            cfg=5.0,
            comfyui_version="0.0.0-stub",
        ),
    )
    return cfg


def test_the_lettering_borrows_the_recipe_of_its_original(tmp_path):
    """加字不碰引擎，能复现靠的是"上一步的参数 + 这句台词" —— 参数从同号的原图借。"""
    cfg = _generated_record(tmp_path)
    source = sticker_png(cfg.paths.output_dir / TODAY.isoformat())

    letter(cfg, source, "你好呀", painter=RecordingPainter(), today=TODAY)

    made = {one.kind: one for one in records(tmp_path)}
    assert made[LETTERED_KIND].seed == 12345
    assert made[LETTERED_KIND].prompt == "chibi blue whale girl"
    assert made[LETTERED_KIND].comfyui_version == "0.0.0-stub", "没借到引擎版本"


def test_the_lettering_will_not_borrow_from_a_sticker_record(tmp_path):
    """同号只剩贴纸那条（原图被清走了）时宁可不借，也不把去背那一站的参数当成配方。

    借贴纸那条会把 `workflows/fish-sticker.json` 与去背模型的文件名写进这条记录，
    写进配方库就是一条假的来路 —— 比留空更坏。
    """
    cfg = _generated_record(tmp_path, kind="sticker")
    source = sticker_png(cfg.paths.output_dir / TODAY.isoformat())

    letter(cfg, source, "你好呀", painter=RecordingPainter(), today=TODAY)

    made = {one.kind: one for one in records(tmp_path)}
    assert made[LETTERED_KIND].seed == 0
    assert made[LETTERED_KIND].workflow == ""
    assert made[LETTERED_KIND].comfyui_version == "未知"


def test_letter_refuses_an_animated_picture(tmp_path):
    gif = tmp_path / "anim.gif"
    gif.write_bytes(b"GIF89a")

    with pytest.raises(ValueError) as caught:
        letter(_cfg(tmp_path), gif, "你好", painter=RecordingPainter(), today=TODAY)

    assert "动图" in str(caught.value)


def test_letter_refuses_an_empty_text(tmp_path):
    with pytest.raises(ValueError) as caught:
        letter(_cfg(tmp_path), sticker_png(tmp_path), "   ", painter=RecordingPainter())

    assert "台词" in str(caught.value)


def test_letter_refuses_to_letter_a_picture_that_already_has_text(tmp_path):
    """再给带字成图加一次字，是往旧台词上叠新台词 —— 那不是"重做"，图就废了。

    落点沿用源图的序号，所以源图是 .captioned.png 时落点就是它自己。
    """
    cfg = _cfg(tmp_path)
    folder = cfg.paths.output_dir / TODAY.isoformat()
    already = sticker_png(folder, name=f"fish-007.{LETTERED_KIND}.png")
    painter = RecordingPainter()

    with pytest.raises(LetteringError) as caught:
        letter(cfg, already, "换一句", painter=painter, today=TODAY)

    assert "已经加过字" in str(caught.value)
    assert ".sticker.png" in str(caught.value), "得说清该拿哪张当源图"
    assert not painter.calls, "拒绝就要拒在画之前"


# ---------- 字体这一环（判据 6） ----------


def test_a_missing_font_is_named_before_anything_is_drawn(tmp_path):
    anywhere = tmp_path / "nope.ttc"
    painter = RecordingPainter()

    with pytest.raises(LetteringError) as caught:
        letter(
            _cfg(tmp_path, font=anywhere),
            sticker_png(tmp_path),
            "你好",
            painter=painter,
            today=TODAY,
        )

    message = str(caught.value)
    assert str(anywhere) in message
    assert "[postprocess].font" in message, "得说清去哪个键里改"
    assert not painter.calls, "字体不对就不该走到画这一步"


def test_a_character_the_font_lacks_is_named_not_drawn_as_a_box(tmp_path):
    """判据 6：缺字形要报出来是哪个字，而不是画成方块。"""
    if not LATIN_ONLY.is_file():
        pytest.skip("本机没有 DejaVuSans，缺字这组用例跳过")
    painter = RecordingPainter()

    with pytest.raises(LetteringError) as caught:
        letter(
            _cfg(tmp_path, font=LATIN_ONLY),
            sticker_png(tmp_path),
            "hello 你好",
            painter=painter,
            today=TODAY,
        )

    message = str(caught.value)
    assert "方块" in message
    assert "你" in message and "好" in message
    assert "U+" in message, "把码位写出来，才好判断该换哪个字体"
    assert not painter.calls


def test_missing_glyphs_is_empty_for_a_font_that_has_everything():
    choice = load_font(any_cjk_font(), 64)

    assert missing_glyphs(opened_font(choice.path, 64, choice.index), "你好呀，世界。") == ()


def test_missing_glyphs_flags_an_emoji():
    """CJK 字体里没有 emoji —— 它也会被画成方块，所以要一起报出来。"""
    choice = load_font(any_cjk_font(), 64)

    assert missing_glyphs(opened_font(choice.path, 64, choice.index), "笑一个😀") == ("😀",)


def test_parse_color_takes_the_forms_the_config_allows():
    assert parse_color("#FFFFFF") == (255, 255, 255, 255)
    assert parse_color("#fff") == (255, 255, 255, 255)
    assert parse_color("#1B1B1B80") == (27, 27, 27, 128)


def test_parse_color_refuses_a_typo():
    with pytest.raises(ConfigError) as caught:
        parse_color("white")

    assert "#RRGGBB" in str(caught.value)


# ---------- 像素（判据 2、3、4，慢） ----------


@pytest.mark.slow
def test_the_painted_picture_carries_the_text_and_a_white_outline(tmp_path):
    """判据 2 与 4：真画一遍 —— 字在、描边是白的、贴纸的透明区没被涂掉。"""
    from PIL import Image

    source = sticker_png(tmp_path)
    made = letter(_cfg(tmp_path), source, "你好呀", today=TODAY)

    assert made.path.is_file(), "带字成图没落盘"
    with Image.open(made.path) as opened:
        image = opened.convert("RGBA")
        assert opened.format == "PNG"
        assert image.size == (480, 480)

    colors = {colour: count for count, colour in image.getcolors(maxcolors=1 << 20)}
    assert (255, 255, 255, 255) in colors, "白描边不见了"
    assert (27, 27, 27, 255) in colors, "字色不是配置里那个"
    assert image.getpixel((0, 0)) == (0, 0, 0, 0), "贴纸的透明角落被涂了"
    assert image.getpixel((120, 120)) == (30, 120, 220, 255), "贴纸本身被改了"

    # 台词压在下方：那片区域原本透明，现在有了墨
    band = image.crop((0, 330, 480, 480))
    inked = sum(1 for pixel in band.get_flattened_data() if pixel[3] > 0)
    assert inked > 5000, f"画面下沿几乎没墨：{inked} 个不透明像素"


def _inked(image, box: tuple[int, int, int, int]) -> int:
    """这块区域里有多少个不透明像素。"""
    return sum(1 for pixel in image.crop(box).get_flattened_data() if pixel[3] > 0)


def _bands(image, colors) -> dict[tuple[int, int, int, int], int]:
    """整张图里这几种颜色各有多少个像素 —— 抗锯齿的中间色不算，只要纯色。"""
    counts = dict.fromkeys(colors, 0)
    for pixel in image.get_flattened_data():
        if pixel in counts:
            counts[pixel] += 1
    return counts


@pytest.mark.slow
def test_the_white_outline_is_what_makes_it_readable_on_a_dark_picture(tmp_path):
    """判据 4 的真账：换一张深色底，字还是看得见的 —— 靠的就是那圈白边。

    这不是"再画一遍看运气"：先按排版算出每行墨框，再逐行核对"字身是字色、
    字身外面围着一圈白"。描边被调没、调成别的色、或者干脆不画，都会掉进断言。
    """
    from PIL import Image

    font_size = 48
    stroke_width = 4
    made = letter(
        _cfg(tmp_path, font_size=font_size, stroke_width=stroke_width),
        dark_png(tmp_path),
        "深色底上也要看得清",
        today=TODAY,
    )

    with Image.open(made.path) as opened:
        image = opened.convert("RGBA")

    ink, fill = made.plan.style.stroke_fill, made.plan.style.fill
    bands = _bands(image, (ink, fill))

    assert bands[ink] > 0, "深色底上一圈白边都没有 —— 字会糊进背景"
    assert bands[fill] > 0, "字身不是配置里那个字色"
    # 白边至少得占墨迹的两成 —— 只是零星几个白点的话，读起来还是糊的。
    share = bands[ink] / (bands[ink] + bands[fill])
    assert share > 0.2, f"白边只占墨迹的 {share:.0%}，压不住深色底"

    # 反过来验一次：把描边调成 0，白边就该彻底消失。
    # 这一条才是"白边是配置要的那层、不是别处漏进来的"的账。
    bare_root = tmp_path / "bare"
    bare_root.mkdir()
    bare = letter(
        _cfg(bare_root, font_size=font_size, stroke_width=0),
        dark_png(bare_root),
        "深色底上也要看得清",
        today=TODAY,
    )
    with Image.open(bare.path) as opened:
        bare_image = opened.convert("RGBA")
    assert _bands(bare_image, (ink,))[ink] == 0, "描边关掉了，画面上还有白 —— 有东西越权"


@pytest.mark.slow
def test_every_line_lands_where_the_plan_said(tmp_path):
    """判据 3 的真账：每一行都得真画在 plan 记的那个坐标上。

    这组断言是照着"坐标错了会怎样"写的，不是照着"画面看着还行"写的 ——
    逐行算墨框（getbbox 认 stroke_width，描边算得进去），再拿相邻两行之间的
    空隙反过来证明行距真的生效了：整块挪上挪下、只画首行、行距被压扁，
    这三种错都活不过去。
    """
    from PIL import Image

    font_size = 32
    stroke_width = 4
    text = "这是一句特别长的台词，一行根本放不下，只好折成好几行，还得保证描边不贴边"
    made = letter(
        _cfg(tmp_path, font_size=font_size, stroke_width=stroke_width),
        blank_png(tmp_path),
        text,
        today=TODAY,
    )
    assert len(made.lines) > 1, "这么长的台词没折行"

    with Image.open(made.path) as opened:
        image = opened.convert("RGBA")

    margin = max(1, round(font_size * 0.5)) + stroke_width
    placed = made.plan.lines
    real = opened_font(made.plan.font.path, font_size, made.plan.font.index)

    boxes = []
    for index, line in enumerate(placed):
        x, y = line.origin
        left, top, right, bottom = real.getbbox(
            line.text, anchor="la", stroke_width=stroke_width
        )
        box = (x + left, y + top, x + right, y + bottom)
        boxes.append(box)
        assert _inked(image, box) > 0, f"第 {index} 行「{line.text}」该在 {box}，那儿没墨"
        assert box[0] >= margin, f"第 {index} 行左缘 {box[0]} 越出字框（边距 {margin}）"
        assert box[2] <= image.width - margin, f"第 {index} 行右缘 {box[2]} 越出字框"
        assert box[1] >= margin, f"第 {index} 行上缘 {box[1]} 顶到画布顶了"
        assert box[3] <= image.height - margin, f"第 {index} 行下缘 {box[3]} 贴到画布底了"

    # 整块墨迹得正好落在各行墨框拼起来的那块区域里。逐行断言抓得住"少画了一行"，
    # 但抓不住"整块被挪了几十像素还压在画布中间"—— 这一条才是那个的账。
    whole = image.getbbox()
    assert whole is not None, "一个字都没画"
    expected = (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
    for name, got, want in zip(("左", "上", "右", "下"), whole, expected, strict=True):
        assert abs(got - want) <= 3, f"整块墨迹的{name}缘在 {got}，排版说的是 {want}"

    # 行与行之间得真的是空的 —— 上一行墨底到下一行墨顶之间不许有墨。
    # 否则"折行"只是把字叠在一起（行距被压扁的错就活不过这一条）。
    for index in range(len(boxes) - 1):
        gap_top = boxes[index][3]
        gap_bottom = boxes[index + 1][1]
        assert gap_bottom > gap_top, f"第 {index}、{index + 1} 行的墨叠在一起了"
        gap = (0, gap_top, image.width, gap_bottom)
        assert _inked(image, gap) == 0, f"第 {index}、{index + 1} 行之间不该有墨"


# ---------- 命令行 ----------


def _twin_repo(tmp_path: Path, font: Path) -> Path:
    """造一个小仓库：真配置 + 真工作流，字体指到本机真字体上。"""
    import shutil

    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "config" / "default.toml", repo / "config" / "default.toml")
    (repo / "config" / "local.toml").write_text(
        f'[postprocess]\nfont = "{font}"\n', encoding="utf-8"
    )
    return repo


@pytest.mark.slow
def test_cli_lettering_writes_a_captioned_png(tmp_path, capsys):
    """判据 1：命令行加字那一站，贴纸进去、带字成图出来。"""
    from lab.cli import main

    repo = _twin_repo(tmp_path, any_cjk_font())
    code = main(["caption", str(sticker_png(tmp_path)), "--text", "你好呀"], repo_root=repo)  # glossary-ok: caption 是 docs/roadmap.md:165 定死的子命令名

    out = capsys.readouterr().out
    assert code == 0, out
    assert "加字完成" in out
    produced = repo / "output" / date.today().isoformat() / "fish-007.captioned.png"
    assert produced.is_file()


def test_cli_lettering_reports_a_missing_font(tmp_path, capsys):
    from lab.cli import main

    repo = _twin_repo(tmp_path, tmp_path / "nope.ttc")
    code = main(["caption", str(sticker_png(tmp_path)), "--text", "你好呀"], repo_root=repo)  # glossary-ok: 子命令名，docs/roadmap.md:165 定死的对外接口

    assert code == 1
    err = capsys.readouterr().err
    assert "加字不成" in err
    assert "[postprocess].font" in err


def test_cli_lettering_needs_the_text_flag(tmp_path):
    from lab.cli import main

    with pytest.raises(SystemExit) as caught:
        main(["caption", str(sticker_png(tmp_path))], repo_root=_twin_repo(tmp_path, any_cjk_font()))  # glossary-ok: 缺 --text 就该被 argparse 挡下，这里的 caption 是子命令名

    assert caught.value.code == 2
