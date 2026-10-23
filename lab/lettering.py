"""后处理第二站：把贴纸变成带台词成图。

加字是纯本地的图像动作（docs/design.md §4「加字层」），不经过引擎 —— 跟去背那一站不一样：
不需要 ComfyUI，只需要一个字体和 Pillow。

这里分成两层，好让"交给画字后端的话对不对"能被单独断言：

- `plan_lines` 只算排版：折成几行、每行的坐标、用哪个字体多大字号、什么颜色、
  描边多宽、存成什么格式。它靠一个 `measure` 回调量字宽 —— 生产传字体的
  `getlength`，测试传一个假的，于是折行逻辑不用真字体也能测。
- `letter` 负责粘合：读配置、挑字体、验字形齐不齐、算排版、把排版交给一个可注入
  的画字后端，最后落成 `fish-###.captioned.png`。

字体是本项目最脆的一环：缺字体、选错 face、字体里没有那个字，Pillow 都会安安静静
画成一个方块，像素比对很难抓。所以这里把所有"字对不对"的判断都做在画之前。

配置写错（字号不是正数、颜色拼错）抛 `config.ConfigError` —— 那和"TOML 语法坏了"
是同一类麻烦，都该由用户去改 `config/` 里那两行，而不是当成"这张图画不出来"。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config import Config, ConfigError
from .naming import folder_for, plan_output, stage_of
from .paths import check_still_image
from .recording import entry_from_base, find_base, used_serials
from .registry import RECORD_NAME, load_entries, write_entry

if TYPE_CHECKING:
    from PIL import ImageFont

__all__ = [
    "CJK_FONT_CANDIDATES",
    "FontChoice",
    "InkStyle",
    "LETTERED_KIND",
    "LetteringError",
    "LetteringPlan",
    "MadeLettering",
    "Painter",
    "PillowPainter",
    "PlacedLine",
    "ink_style",
    "letter",
    "load_font",
    "missing_glyphs",
    "parse_color",
    "plan_lines",
    "wrap_text",
]

#: 带字成图的后缀：`fish-007.captioned.png` 里那一段（docs/roadmap.md:159 定死的
#: 对外命名，改不得）。命令行子命令也叫 caption，那是同一个对外名字。  # glossary-ok: caption 是 docs/roadmap.md:165 定死的对外子命令名
LETTERED_KIND = "captioned"

#: 本机找得到就能直接开工的中文字体。按顺序挑第一个存在的；都不是就用报错信息
#: 把该配哪儿说清楚（config/default.toml 里 [postprocess].font 留空，带本机路径的
#: 实际值写在 config/local.toml）。
CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)

#: TTC 里最多试这么多个 face 就停手。Noto 的 CJK 合集是 5 个。
_MOST_FACES = 16

#: 拿它当"缺字长什么样"的尺子。U+FFFF 是永不分配的码位，任何字体都不会有它的
#: 字形，所以它画出来必然是那个方块 —— 台词里哪个字画出来的遮罩跟它一模一样，
#: 那个字就是缺的。
_TOFU_PROBE = "\uffff"

RGBA = tuple[int, int, int, int]


class LetteringError(Exception):
    """这张图加不上字：字体找不到、字体里没有那个字、排版放不下。"""


@dataclass(frozen=True)
class FontChoice:
    """用的是哪个字体文件、里面第几个 face。

    单独拎出来是为了被断言：换字体不报错、换 face 不报错，"用了哪个"只能靠记下来。
    字号不在这里 —— 那是样式的事（`InkStyle.font_size`），同一个字体可以配不同字号。
    """

    path: Path
    index: int


@dataclass(frozen=True)
class PlacedLine:
    """一行台词落在画布的哪一点。origin 是这一行的左上角（Pillow 的 anchor="la"）。"""

    text: str
    origin: tuple[int, int]


@dataclass(frozen=True)
class InkStyle:
    """一句话该长什么样。颜色都已经解析成 RGBA，画的时候不用再想。"""

    font_size: int
    fill: RGBA
    stroke_width: int
    stroke_fill: RGBA


@dataclass(frozen=True)
class LetteringPlan:
    """一次加字要做的全部决定 —— 画字后端照着它画，不做任何判断。"""

    text: str
    lines: tuple[PlacedLine, ...]
    font: FontChoice
    style: InkStyle
    #: 坐标怎么理解 —— Pillow 的 anchor 词。`la` 是"原点在这一行文字的左侧基线
    #: 之上"，也就是 PlacedLine.origin 说的那个左上角。换后端就得照这个词解释坐标。
    anchor: str = "la"
    image_format: str = "PNG"


@dataclass(frozen=True)
class MadeLettering:
    """一张带台词的成图。

    from_image 记的是贴纸那张图 —— 同序号，"这张带字图是从哪张贴纸来的"就靠它。
    plan 留着整份排版决定：事后想核对"这句台词该落在哪、用什么字体"，不必再猜。
    """

    path: Path
    serial: int
    from_image: Path
    plan: LetteringPlan

    @property
    def id(self) -> str:
        return f"fish-{self.serial:03d}"

    @property
    def text(self) -> str:
        return self.plan.text

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(line.text for line in self.plan.lines)


class Painter(Protocol):
    """画字后端：给一张源图、一份排版、一个落点，把它画出来。"""

    def paint(self, source: Path, plan: LetteringPlan, target: Path) -> None: ...


class PillowPainter:
    """生产用的画字后端：Pillow 打开源图、画字、存 PNG。"""

    def paint(self, source: Path, plan: LetteringPlan, target: Path) -> None:
        from PIL import Image, ImageDraw

        font = _open_font(plan.font, plan.style.font_size)
        with Image.open(source) as opened:
            canvas = opened.convert("RGBA")
        draw = ImageDraw.Draw(canvas)
        for line in plan.lines:
            draw.text(
                line.origin,
                line.text,
                font=font,
                fill=plan.style.fill,
                stroke_width=plan.style.stroke_width,
                stroke_fill=plan.style.stroke_fill,
                anchor=plan.anchor,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(target, format=plan.image_format)


def parse_color(value: str) -> RGBA:
    """把 `#RRGGBB` / `#RRGGBBAA` / `#RGB` 解析成 RGBA。

    写错了直接说清楚，别让它悄悄画成透明。这是配置值的解析，所以写错算
    `ConfigError` —— 改的是 `config/` 里那一行，不是这张图。
    """
    text = value.strip()
    if not text.startswith("#"):
        raise ConfigError(
            f"颜色要写成 #RRGGBB 的样子，这是 {value!r} —— "
            "在 config/default.toml 的 [postprocess] 里改。"
        )
    digits = text[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) not in (6, 8):
        raise ConfigError(
            f"颜色要写成 #RRGGBB 或 #RRGGBBAA，这是 {value!r} —— "
            "在 config/default.toml 的 [postprocess] 里改。"
        )
    try:
        parts = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
    except ValueError as exc:
        raise ConfigError(f"颜色里有不是十六进制的字符：{value!r}") from exc
    if len(parts) == 3:
        parts.append(255)
    red, green, blue, alpha = parts
    return red, green, blue, alpha


def ink_style(cfg: Config) -> InkStyle:
    """把配置里的字号、颜色、描边读成一份画字用的样式。

    三个值有任何一个是负数或零就报错，不悄悄改成一个"看起来能用"的值 ——
    配置和做出来的图对不上，比当场报错难查得多。
    """
    post = cfg.postprocess
    if post.font_size <= 0:
        raise ConfigError(
            f"字号要是正数，这是 {post.font_size} —— "
            "在 config/default.toml 的 [postprocess].font_size 改一个正数。"
        )
    if post.stroke_width < 0:
        raise ConfigError(
            f"描边宽度不能是负数，这是 {post.stroke_width} —— "
            "在 config/default.toml 的 [postprocess].stroke_width 改，0 就是不要描边。"
        )
    return InkStyle(
        font_size=post.font_size,
        fill=parse_color(post.fill),
        stroke_width=post.stroke_width,
        stroke_fill=parse_color(post.stroke_color),
    )


def load_font(path: Path | str, size: int, index: int | None = None) -> FontChoice:
    """挑定一个字体文件里的哪个 face，返回这份选择。

    index=None 时自己挑：TTC 是字体合集，里面有 JP/KR/SC/TC/HK 好几份，默认那份
    是日文 —— 中文字形用的是日文字体里那套，观感是错的。所以这里优先挑 family
    以 SC 结尾的那个；单文件字体就一个 face，index 只能是 0。

    size 只用来探测（要真的打开才知道 face 能不能用），不写进返回值。
    这里只挑不验 —— 真打开由 `_open_font` 负责，它按 (选择, 字号) 缓存结果，
    所以一趟加字不会把同一个 face 反复开好几遍。
    """
    font_path = Path(path).expanduser()
    if not font_path.is_file():
        raise LetteringError(
            f"加字用的字体不在：{font_path} —— 在 config/local.toml 的 "
            "[postprocess].font 里写一个真实存在的绝对路径。"
        )

    chosen = _preferred_index(font_path, size) if index is None else index
    return FontChoice(path=font_path, index=chosen)


@cache
def _open_font(choice: FontChoice, size: int) -> ImageFont.FreeTypeFont:
    """按已经定好的选择打开字体。开着这一步只做一件事 —— 真的拿到字体对象。

    结果按 (选择, 字号) 缓存：量字宽、验字形、交给画字后端都问同一份字体，
    没必要把同一个 face 反反复复打开。FreeType 的字体对象本来就是只读共享的。
    """
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(choice.path), size=size, index=choice.index)
    except OSError as exc:
        raise LetteringError(
            f"字体打不开：{choice.path}（第 {choice.index} 个 face）—— {exc}。"
            "单文件字体只有第 0 个 face；TTC 才有后面几个。"
        ) from exc


@cache
def _preferred_index(path: Path, size: int) -> int:
    """TTC 里挑简体中文那个 face；单文件字体没得挑，就用唯一的那个。

    挑不到为什么要报错而不是悄悄用第 0 个：合集里第 0 个常常是日文（Noto 就是），
    日文字体有大量简体字是"借来的字形"，少数写法不一样 —— 画出来能看但不对，
    而且没人会注意到。宁可明说"这份字体里没有简体中文"。

    结果按 (路径, 字号) 缓存 —— 探测要挨个试打开 face，一次加字探一遍就够了。
    """
    from PIL import ImageFont

    famlies: list[str] = []
    for index in range(_MOST_FACES):
        try:
            face = ImageFont.truetype(str(path), size=size, index=index)
        except OSError:
            break
        family = face.getname()[0] or ""
        if family.endswith("SC") or " SC " in family:
            return index
        famlies.append(f"第 {index} 个是 {family or '无名'}")
    if len(famlies) > 1:
        raise LetteringError(
            f"{Path(path).name} 里有 {len(famlies)} 个 face，没有一个是简体中文的"
            f"（{'、'.join(famlies)}）—— 在 config/local.toml 的 [postprocess].font "
            "指一份含 SC 的字体，或者把 font 留空让它按常见位置挑。"
        )
    return 0


def _mask_signature(
    font: ImageFont.FreeTypeFont, text: str
) -> tuple[tuple[int, int], bytes]:
    """一段字画出来的遮罩指纹：尺寸 + 字节。字形不同指纹就不同。

    ImagingCore 自己不给字节，包成 Image 才取得出来（Pillow 12 没有 tobytes）。
    """
    from PIL import Image

    mask = font.getmask(text)
    return mask.size, Image.Image()._new(mask).tobytes()


def missing_glyphs(font: ImageFont.FreeTypeFont, text: str) -> tuple[str, ...]:
    """台词里哪些字这个字体画不出来（会变成方块）。原序返回，不重复。

    判据是"画出来的遮罩跟 U+FFFF 的一样"。比像素比对稳，也不用事先知道字体里
    有哪些字。
    """
    probe = _mask_signature(font, _TOFU_PROBE)
    missing: list[str] = []
    for ch in text:
        if ch.isspace() or ch in missing:
            continue
        if _mask_signature(font, ch) == probe:
            missing.append(ch)
    return tuple(missing)


def wrap_text(
    text: str, *, measure: Callable[[str], float], limit: float
) -> tuple[str, ...]:
    """按可用宽度折行。

    `measure` 是"这段字有多宽"——生产是字体的 getlength，测试可以给个假的。
    台词里自己打的换行照原样保留；一段里放不下时，英文尽量在空格处断，中文
    没有空格就按字断。单个字就比可用宽度还宽，那是画布/字号配错了，直接报错。
    """
    if limit <= 0:
        raise LetteringError(
            f"画布太窄：留给台词的宽度只有 {limit:.0f}px —— "
            "在 config/default.toml 的 [postprocess].font_size 调小一点。"
        )

    lines: list[str] = []
    for paragraph in text.splitlines():
        stripped = paragraph.strip()
        if stripped:
            lines.extend(_wrap_paragraph(stripped, measure=measure, limit=limit))
    if not lines:
        raise LetteringError("台词里没有能印的字。")
    return tuple(lines)


def _wrap_paragraph(
    paragraph: str, *, measure: Callable[[str], float], limit: float
) -> list[str]:
    """把一段字塞进 limit 宽，返回若干行，每一行都真的不超过 limit。

    一行塞不下时优先断在最后一个空格上（英文别劈词）；没有空格或断完还剩尾巴，
    就接着断 —— 断一次不一定够，`aaaaaaWWW` 这种长词不断几刀还是超宽。
    """
    lines: list[str] = []
    current = ""
    for ch in paragraph:
        if measure(current + ch) <= limit:
            current += ch
            continue
        if ch == " " and current:
            # 正好塞满一整行的最后一个词：这个空格就是断点，别把它带到下一行。
            lines.append(current)
            current = ""
            continue
        current += ch
        while measure(current) > limit:
            cut = current.rfind(" ")
            if cut > 0:
                lines.append(current[:cut])
                current = current[cut + 1 :]
            elif len(current) > 1:
                lines.append(current[:-1])
                current = current[-1:]
            else:
                raise LetteringError(
                    f"画布太窄：{ch!r} 一个字就宽过可用宽度 {limit:.0f}px —— "
                    "在 config/default.toml 的 [postprocess].font_size 调小一点。"
                )
    if current:
        lines.append(current)
    return lines


def plan_lines(
    text: str,
    *,
    measure: Callable[[str], float],
    line_height: int,
    canvas: tuple[int, int],
    font: FontChoice,
    style: InkStyle,
) -> LetteringPlan:
    """算出一句话画在哪儿、画成什么样。

    位置：贴底居中。台词压在画面下沿，离底留半个字高的边；多行时整块从底往上堆。
    放不下（行数太多顶出画布）直接报错 —— 静默把字画出画布之外比报错难查得多。
    """
    width, height = canvas
    # 边距要把描边算进去：描边是往字身外面长的，只按字身留半个字高的话，
    # 一行正好排满时描边就贴在画布边上了。
    margin = max(1, round(style.font_size * 0.5)) + style.stroke_width
    lines = wrap_text(text, measure=measure, limit=width - 2 * margin)

    block = line_height * len(lines)
    top = height - margin - block
    if top < margin:
        raise LetteringError(
            f"台词太长：折成 {len(lines)} 行，画布高 {height}px 放不下 —— "
            "缩短台词，或在 config/default.toml 的 [postprocess].font_size 调小一点。"
        )

    placed = tuple(
        PlacedLine(
            text=line,
            origin=(round((width - measure(line)) / 2), top + index * line_height),
        )
        for index, line in enumerate(lines)
    )
    return LetteringPlan(text=text, lines=placed, font=font, style=style)


def _font_path(cfg: Config) -> Path:
    """用哪个字体文件：配了就用配的，没配就在本机常见位置里挑一个。"""
    configured = cfg.postprocess.font
    if configured is not None:
        if not configured.is_file():
            raise LetteringError(
                f"加字用的字体不在：{configured} —— "
                "config/local.toml 的 [postprocess].font 里改一个真实存在的路径，"
                "或者删掉那一行让它自动挑（本机常见位置：" + "、".join(CJK_FONT_CANDIDATES) + "）。"
            )
        return configured
    for candidate in CJK_FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return path
    raise LetteringError(
        "没找到能写中文的字体 —— 在 config/local.toml 的 [postprocess].font 里写一个"
        "绝对路径。找过了这些位置：" + "、".join(CJK_FONT_CANDIDATES)
    )


def _missing_message(missing: tuple[str, ...], font_path: Path) -> str:
    marks = "、".join(f"{ch}（U+{ord(ch):04X}）" for ch in missing)
    return (
        f"这个字体里没有这些字，画出来会是方块：{marks} —— 换一个覆盖更广的中文字体"
        f"（现在用的是 {font_path}），或者在 config/local.toml 的 [postprocess].font 换一个。"
    )


def _already_lettered(source: Path) -> bool:
    """这张图是不是已经带字了。看名字里那一段：`fish-007.captioned.png`。"""
    return stage_of(source) == LETTERED_KIND


def letter(
    cfg: Config,
    image: Path | str,
    text: str,
    *,
    painter: Painter | None = None,
    today: date | None = None,
) -> MadeLettering:
    """给一张贴纸印上一句台词，落成 `fish-###.captioned.png`。

    源图是贴纸（去好背的透明底图），成图跟它同目录、同序号 —— 原图、贴纸、
    带字成图三张摆在一起，对着看很方便。
    """
    from PIL import Image

    source = check_still_image(image, what="图片", why="加不了字")
    if not text or not text.strip():
        raise ValueError("台词是空的 —— 用 --text 给一句要印上去的话。")
    # 加过的图不能再加：落点沿用源图的序号，给一张 .captioned.png 加字会落到它自己
    # 身上，那不是"重做一遍"，是把新台词叠在旧台词上 —— 图还在，字已经糊了。
    if _already_lettered(source):
        raise LetteringError(
            f"这张已经加过字了：{source.name} —— 加字要拿贴纸（.sticker.png）当源图，"
            "换一句台词就换一张贴纸重来。"
        )

    style = ink_style(cfg)
    font_path = _font_path(cfg)
    choice = load_font(font_path, style.font_size)
    font = _open_font(choice, style.font_size)

    missing = missing_glyphs(font, text)
    if missing:
        raise LetteringError(_missing_message(missing, font_path))

    ascent, descent = font.getmetrics()
    line_height = ascent + descent + round(style.font_size * 0.15)

    # Image.open 是惰性的：这一步只读文件头拿到宽高，不解码像素。
    with Image.open(source) as opened:
        canvas = opened.size

    plan = plan_lines(
        text,
        measure=font.getlength,
        line_height=line_height,
        canvas=canvas,
        font=choice,
        style=style,
    )
    # 账先读一遍：源图是贴着号的文件时，落点沿用它的号；源图不在账上（外来图）
    # 就按"那个目录里最大号接着排"，这时光看目录会撞号 —— 撞出来的同号会把上一条
    # 无关生成的种子借过来（判据 ⑥）。读的是**落点那一本**：给昨天的贴纸加字，
    # 落点在昨天那个目录，来路那一条也只记在那一本里。
    ledger = folder_for(cfg.paths.output_dir, source, today) / RECORD_NAME
    entries = load_entries(ledger)
    target, serial = plan_output(
        cfg.paths.output_dir, source, today, LETTERED_KIND, known=used_serials(entries)
    )
    (painter or PillowPainter()).paint(source, plan, target)
    made = MadeLettering(path=target, serial=serial, from_image=source, plan=plan)

    # 加字这一站不碰引擎，能复现的是"上一步的参数 + 这句台词"：生成那几栏从同号的
    # 贴纸/原图那条借，画布尺寸手里就有（加字不换尺寸），不必再去读一次盘。
    base = find_base(entries, made.id)
    write_entry(
        ledger,
        entry_from_base(
            cfg,
            base,
            ident=made.id,
            path=target,
            kind=LETTERED_KIND,
            workflow=base.workflow if base is not None else "",
            model=base.model if base is not None else "",
            caption=text,  # glossary-ok: 字段名照 docs/design.md §6.2
            size=canvas,
        ),
    )
    return made
