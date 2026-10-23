"""后处理的行为：一张图进去，透明底贴纸出来（票 #9）。

和 test_pipeline 一个路子：对着 stub 跑一条真端到端（真 HTTP、真搬文件），对着
记录型假引擎看"交给引擎的话对不对"。去背的推理在引擎里（ComfyUI-RMBG 的节点），
工作台只说清两件事 —— 喂哪张图、成品叫什么。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from lab.comfy import EngineClient, EngineError, GeneratedImage, JobResult
from lab.config import load_config
from lab.postprocess import sticker
from lab.stub import StubEngine

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 24)


def png_bytes(mode: str = "RGB") -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    if mode == "RGBA":
        # 中间不透明、四周全透明 —— 这张图自己就证明了"透明区留下来了"
        image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((4, 4, 11, 11), fill=(30, 120, 220, 255))
    else:
        image = Image.new("RGB", (16, 16), (10, 20, 30))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def picture(tmp_path: Path, name: str = "pic.png") -> Path:
    """一张真的 PNG 当源图 —— 上传那段要读它的字节。"""
    path = tmp_path / name
    path.write_bytes(png_bytes())
    return path


def _cfg(tmp_path: Path, output_dir: Path | None = None):
    from dataclasses import replace

    cfg = load_config(REPO_ROOT)
    return replace(
        cfg,
        paths=replace(cfg.paths, output_dir=output_dir or tmp_path / "output"),
    )


class RecordingStickerEngine:
    """记下提交的 prompt，回的是一张真 RGBA PNG。

    成品要过 alpha 校验，所以这里不能拿假字节糊弄 —— fetch 落下去的得是能读的图。
    """

    def __init__(self, image: bytes | None = None) -> None:
        self.prompts: list[dict] = []
        self.uploads: list[Path] = []
        self.upload_names: list[str] = []
        self.image = image if image is not None else png_bytes("RGBA")

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        _ = client_id
        self.prompts.append(prompt)
        return "job-1"

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        _ = on_poll
        image = GeneratedImage(
            filename="fish/sticker_00001_.png", subfolder="", kind="output", node="3"
        )
        return JobResult(prompt_id=prompt_id, images=(image,), files=(), history={})

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(image.filename).name
        target.write_bytes(self.image)
        return target

    def upload_image(self, path: Path, name: str | None = None) -> str:
        self.uploads.append(Path(path))
        resolved = name or Path(path).name
        self.upload_names.append(resolved)
        return resolved


def _node_by_title(prompt: dict, title: str) -> dict:
    from lab.workflows import find_by_title

    node_id = find_by_title(prompt, title)
    assert node_id is not None, f"交出去的 prompt 里没有标题「{title}」"
    return prompt[node_id]


def _stub_client(engine: StubEngine) -> EngineClient:
    return EngineClient(engine.host, engine.port, poll_interval=0.01, max_polls=50)


# ---------- 落盘与命名 ----------


def test_a_picture_from_the_workbench_keeps_its_serial_and_folder(tmp_path):
    """工作台出的图 → 贴纸挨着它落，序号不变：fish-007.orig → fish-007.sticker。"""
    folder = tmp_path / "output" / "2026-10-23"
    folder.mkdir(parents=True)
    origin = folder / "fish-007.orig.png"
    origin.write_bytes(png_bytes())

    engine = RecordingStickerEngine()
    made = sticker(_cfg(tmp_path), origin, client=engine, today=TODAY)

    assert made.path == folder / "fish-007.sticker.png"
    assert made.id == "fish-007"
    assert made.serial == 7
    assert made.path.read_bytes() == engine.image


def test_a_picture_from_elsewhere_lands_in_todays_folder(tmp_path):
    """验收判据 3：素材图（不是工作台出的）照样能去背，落到今天那个目录。"""
    engine = RecordingStickerEngine()
    made = sticker(
        _cfg(tmp_path), picture(tmp_path), client=engine, today=TODAY
    )

    assert made.path == tmp_path / "output" / "2026-10-24" / "fish-001.sticker.png"
    assert made.from_image == (tmp_path / "pic.png").resolve()


def test_a_foreign_picture_takes_the_next_serial_of_that_day(tmp_path):
    folder = tmp_path / "output" / "2026-10-24"
    folder.mkdir(parents=True)
    (folder / "fish-003.sticker.png").write_bytes(png_bytes("RGBA"))

    made = sticker(
        _cfg(tmp_path), picture(tmp_path), client=RecordingStickerEngine(), today=TODAY
    )

    assert made.path.name == "fish-004.sticker.png"


def test_redoing_a_picture_overwrites_its_sticker(tmp_path):
    """源图有号时，重做就是覆盖它原来那张 —— 那就是"把这张重来一遍"的意思。"""
    folder = tmp_path / "output" / "2026-10-24"
    folder.mkdir(parents=True)
    origin = folder / "fish-001.orig.png"
    origin.write_bytes(png_bytes())
    (folder / "fish-001.sticker.png").write_bytes(b"stale")

    engine = RecordingStickerEngine()
    made = sticker(_cfg(tmp_path), origin, client=engine, today=TODAY)

    assert made.path.name == "fish-001.sticker.png"
    assert made.path.read_bytes() == engine.image


def test_sticker_walks_the_whole_way_against_the_stub_engine(tmp_path):
    """验收判据 5：无显卡也能整条走通 —— 上传 → 提交 → 轮询 → 取回 → 落盘。"""
    from PIL import Image

    with StubEngine(mode="RGBA") as engine:
        made = sticker(
            _cfg(tmp_path),
            picture(tmp_path),
            client=_stub_client(engine),
            today=TODAY,
        )

    assert made.path.exists()
    with Image.open(made.path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0


# ---------- 全量记录（票 #11） ----------


def records(tmp_path: Path):
    from lab.registry import load_entries, record_path

    return load_entries(record_path(tmp_path / "output", TODAY))


def test_the_sticker_lands_in_the_record(tmp_path):
    """验收判据 1：去背出来的这张也要能在当天账里查到。"""
    sticker(_cfg(tmp_path), picture(tmp_path), client=RecordingStickerEngine(), today=TODAY)

    (only,) = records(tmp_path)
    assert only.id == "fish-001"
    assert only.kind == "sticker"
    assert only.file.endswith("2026-10-24/fish-001.sticker.png")
    assert (only.width, only.height) == (16, 16)


def test_the_sticker_records_which_template_made_it(tmp_path):
    """复现要知道是哪只模型去背的 —— 记的是模板里那只，不是文件后缀。"""
    sticker(_cfg(tmp_path), picture(tmp_path), client=RecordingStickerEngine(), today=TODAY)

    (only,) = records(tmp_path)
    assert only.workflow == "workflows/fish-sticker.json"
    assert only.model == "Lucida"


def test_the_sticker_borrows_the_recipe_of_its_original(tmp_path):
    """贴纸的 prompt 与种子不是它自己的，是它那张原图的 —— 从当天账里借。"""
    from lab.registry import Entry, record_path, write_entry

    folder = tmp_path / "output" / TODAY.isoformat()
    folder.mkdir(parents=True)
    origin = folder / "fish-001.orig.png"
    origin.write_bytes(png_bytes())
    write_entry(
        record_path(tmp_path / "output", TODAY),
        Entry(
            id="fish-001",
            created_at="2026-10-24T02:00:00+08:00",
            kind="orig",
            file="fish-001.orig.png",
            width=16,
            height=16,
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

    sticker(_cfg(tmp_path), origin, client=RecordingStickerEngine(), today=TODAY)

    made = {one.kind: one for one in records(tmp_path)}
    assert made["sticker"].seed == 12345
    assert made["sticker"].prompt == "chibi blue whale girl"
    assert made["sticker"].comfyui_version == "0.0.0-stub"


# ---------- 交给引擎的话 ----------


def test_sticker_feeds_the_uploaded_copy_and_pins_the_model(tmp_path):
    engine = RecordingStickerEngine()
    origin = picture(tmp_path, "源 图.png")

    made = sticker(_cfg(tmp_path), origin, client=engine, today=TODAY)

    assert engine.uploads == [origin.resolve()]
    assert engine.upload_names[0].startswith("lab-init-")

    prompt = engine.prompts[0]
    assert _node_by_title(prompt, "图片")["inputs"]["image"] == engine.upload_names[0]
    assert _node_by_title(prompt, "去背")["inputs"]["model"] == "Lucida"
    assert made.model == "Lucida"
    assert made.prompt_id == "job-1"
    assert made.engine_filename == "fish/sticker_00001_.png"


def test_a_foreign_name_does_not_steal_a_serial(tmp_path):
    """验收判据 3 的边界：IMG-007.jpg 不是 7 号。

    它要是被当成 7 号，一张外来图就会盖掉当天已有的 fish-007.sticker.png。
    （这里文件名与字节故意不一致：引擎那一段不解析内容，只看后缀。）
    """
    folder = tmp_path / "output" / "2026-10-24"
    folder.mkdir(parents=True)
    existed = folder / "fish-007.sticker.png"
    existed.write_bytes("先来的那张".encode())

    made = sticker(
        _cfg(tmp_path),
        picture(tmp_path, "IMG-007.jpg"),
        client=RecordingStickerEngine(),
        today=TODAY,
    )

    assert made.path.name == "fish-008.sticker.png"
    assert existed.read_bytes() == "先来的那张".encode()


# ---------- 出错的那几条路 ----------


def test_a_result_without_alpha_is_refused(tmp_path):
    """接成 MASK_IMAGE 那一路能跑完但不透明 —— 不透明的图不许冒充贴纸落盘。"""
    engine = RecordingStickerEngine(image=png_bytes("RGB"))

    with pytest.raises(EngineError) as caught:
        sticker(_cfg(tmp_path), picture(tmp_path), client=engine, today=TODAY)

    assert "alpha" in str(caught.value)
    assert not (tmp_path / "output").exists(), "没验过就落盘了"


def test_an_animated_picture_is_refused(tmp_path):
    """验收判据 4 的边界：动图这一阶段不做（docs/roadmap.md §3「风险」）。"""
    gif = tmp_path / "anim.gif"
    gif.write_bytes(b"GIF89a")

    with pytest.raises(ValueError) as caught:
        sticker(_cfg(tmp_path), gif, client=RecordingStickerEngine())

    assert "动图" in str(caught.value)


def test_sticker_refuses_a_picture_that_is_not_there(tmp_path):
    """验收判据 4：图不在就说清楚，别甩一段看不懂的异常。"""
    with pytest.raises(ValueError) as caught:
        sticker(_cfg(tmp_path), tmp_path / "nope.png", client=RecordingStickerEngine())

    assert "图片不在" in str(caught.value)


def test_sticker_refuses_an_unsupported_format(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("这不是图", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        sticker(_cfg(tmp_path), note, client=RecordingStickerEngine())

    assert "图片格式不支持" in str(caught.value)


def test_sticker_wants_a_template_configured(tmp_path):
    from dataclasses import replace

    cfg = _cfg(tmp_path)
    cfg = replace(cfg, workflows=replace(cfg.workflows, sticker=""))

    with pytest.raises(ValueError) as caught:
        sticker(cfg, picture(tmp_path), client=RecordingStickerEngine())

    assert "[workflows].sticker" in str(caught.value)


# ---------- 命令行 ----------


def _twin_repo(tmp_path: Path, port: int) -> Path:
    """造一个小仓库：真配置 + 真工作流，只有引擎端口指到 stub 上。

    与 test_pipeline 里那份是同一套手法 —— 跨文件测试工厂不抽 conftest，
    这点重复换来的是两个文件各读各的。
    """
    import shutil

    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "config" / "default.toml", repo / "config" / "default.toml")
    shutil.copytree(REPO_ROOT / "workflows", repo / "workflows")
    (repo / "config" / "local.toml").write_text(
        f'[comfy]\nhost = "127.0.0.1"\nport = {port}\n', encoding="utf-8"
    )
    return repo


def test_cli_sticker_writes_an_rgba_png(tmp_path, capsys):
    """验收判据 1 与 2：命令跑通，成品确实是带 alpha 通道的 RGBA。"""
    from PIL import Image

    from lab.cli import main

    with StubEngine(mode="RGBA") as engine:
        repo = _twin_repo(tmp_path, engine.port)
        code = main(["sticker", str(picture(tmp_path))], repo_root=repo)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "fish-001.sticker.png" in out

    produced = repo / "output" / date.today().isoformat() / "fish-001.sticker.png"
    with Image.open(produced) as image:
        assert image.mode == "RGBA"
        assert image.size == (64, 64)  # stub 回的那张
        assert image.getpixel((0, 0))[3] == 0  # 角落还是透明的
        assert image.getpixel((32, 32))[3] == 255  # 中间那块还在


def test_cli_sticker_reports_a_picture_that_is_not_there(tmp_path, capsys):
    from lab.cli import main

    code = main(
        ["sticker", str(tmp_path / "nope.png")], repo_root=_twin_repo(tmp_path, 1)
    )

    assert code == 2
    assert "图片不在" in capsys.readouterr().err


def test_cli_sticker_needs_a_picture(tmp_path, capsys):
    """没给图就当用错命令（退出码 2），不是默默挑一张。"""
    from lab.cli import main

    with pytest.raises(SystemExit) as caught:
        main(["sticker"], repo_root=_twin_repo(tmp_path, 1))

    assert caught.value.code == 2


def test_cli_sticker_reports_an_engine_that_says_no(tmp_path, capsys):
    from lab.cli import main

    with StubEngine(mode="RGBA", reject_prompts=True) as engine:
        repo = _twin_repo(tmp_path, engine.port)
        code = main(["sticker", str(picture(tmp_path))], repo_root=repo)

    assert code == 1
    assert "引擎没干成" in capsys.readouterr().err
