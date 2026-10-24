"""复现：拿配方库里的那条重跑，看它还能不能出回原来那张（票 #12）。

三条边界都在这里钉住：
- **重跑真的走了引擎**，不是把原图拷一份 —— 断言的是交给引擎的 prompt 与落下的新文件；
- **新图也要进当天那本账**，否则「重跑了一次」这件事下次就没人知道；
- **比的是像素，不是文件名**：同种子同模型同引擎版本下应当完全一致，
  差一个像素就说不一致，不拿"看起来差不多"糊过去。

假引擎只出现在 `client` 这个接缝上 —— 和 test_pipeline 对它的态度一样：
替换掉的本来就该是外部物（真引擎），不是被测代码。
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from lab.comfy import GeneratedImage, JobResult
from lab.config import load_config
from lab.registry import (
    Entry,
    EntrySource,
    library_path,
    load_entries,
    record_path,
    write_entry,
)
from lab.reproduce import reproduce

REPO_ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 24)


def png_bytes(color: tuple[int, int, int] = (10, 20, 30)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingEngine:
    """记录提交过什么、回一张指定的图，其余照 EngineClient 的样子说话。"""

    def __init__(self, image: bytes | None = None, version: str = "0.3.40") -> None:
        self.prompts: list[dict] = []
        self.uploads: list[Path] = []
        self._image = png_bytes() if image is None else image
        self._version = version
        self._counter = 0

    def version(self) -> str:
        return self._version

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        _ = client_id
        self.prompts.append(prompt)
        self._counter += 1
        return f"job-{self._counter}"

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        if on_poll is not None:
            on_poll(1, 0.5)
        image = GeneratedImage(
            filename="fish/txt2img_00001_.png", subfolder="", kind="output", node="13"
        )
        return JobResult(prompt_id=prompt_id, images=(image,), files=(), history={})

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(image.filename).name
        target.write_bytes(self._image)
        return target

    def upload_image(self, path: Path, name: str | None = None) -> str:
        self.uploads.append(Path(path))
        return name or Path(path).name


def _repo(tmp_path: Path):
    """小仓库：模板复制一份（要能改），仓库根与输出目录都挪进 tmp。"""
    shutil.copytree(REPO_ROOT / "workflows", tmp_path / "workflows")
    cfg = load_config(REPO_ROOT)
    return replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
    )


def recipe(**overrides) -> Entry:
    base = Entry(
        id="fish-001",
        created_at="2026-10-24T02:05:43+08:00",
        kind="orig",
        file="output/2026-10-24/fish-001.orig.png",
        width=8,
        height=8,
        workflow="workflows/fish-txt2img.json",
        model="flux-2-klein-base-4b-fp8.safetensors",
        prompt="chibi blue whale girl",
        negative="lowres",
        seed=12345,
        steps=20,
        cfg=5.0,
        comfyui_version="0.3.40",
        requested_width=1024,
        requested_height=1024,
    )
    return replace(base, **overrides)


def kept(cfg, entry: Entry) -> Entry:
    """把这条写进配方库，顺便把原图落在它自己说的位置上。"""
    picture = Path(cfg.repo_root) / entry.file
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(png_bytes())
    write_entry(library_path(cfg.repo_root), entry, library=True)
    return entry


def _run(tmp_path: Path, *, engine=None, recipe_kwargs=None, **kwargs):
    cfg = _repo(tmp_path)
    kept(cfg, recipe(**(recipe_kwargs or {})))
    return reproduce(
        cfg,
        "fish-001",
        client=engine or RecordingEngine(),
        today=TODAY,
        **kwargs,
    )


# ---------- 重跑真的跑了一趟 ----------


def test_it_brings_back_a_picture(tmp_path):
    report = _run(tmp_path)

    assert report.image.path.is_file()
    assert report.image.path.read_bytes() == png_bytes()
    # 新号，不顶掉原图那条 —— 重跑是又出一次，不是改档案。
    assert report.image.id == "fish-002"


def test_it_feeds_the_engine_the_parameters_from_the_recipe(tmp_path):
    engine = RecordingEngine()
    _run(tmp_path, engine=engine)

    (prompt,) = engine.prompts
    by_title = {
        (node.get("_meta") or {}).get("title"): node
        for node in prompt.values()
        if (node.get("_meta") or {}).get("title")
    }
    assert by_title["提示词"]["inputs"]["text"] == "chibi blue whale girl"
    assert by_title["负面词"]["inputs"]["text"] == "lowres"
    assert by_title["种子"]["inputs"]["noise_seed"] == 12345
    assert by_title["步数"]["inputs"]["steps"] == 20
    assert by_title["尺寸"]["inputs"]["width"] == 1024


def test_the_rerun_lands_in_the_days_record(tmp_path):
    cfg = _repo(tmp_path)
    kept(cfg, recipe())
    reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    entries = load_entries(record_path(cfg.paths.output_dir, TODAY))
    assert [one.id for one in entries if one.id == "fish-002"]
    rerun = entries[0]
    assert (rerun.seed, rerun.steps, rerun.cfg) == (12345, 20, 5.0)
    assert rerun.workflow == "workflows/fish-txt2img.json"
    assert rerun.model == "flux-2-klein-base-4b-fp8.safetensors"


# ---------- 一致 / 不一致，都不许含糊 ----------


def test_it_says_the_picture_matches(tmp_path):
    report = _run(tmp_path)
    assert report.same is True


def test_it_says_the_picture_differs(tmp_path):
    engine = RecordingEngine(image=png_bytes((200, 10, 10)))
    report = _run(tmp_path, engine=engine)

    assert report.same is False
    # 不一致也得把图留下来：人要看得见差在哪儿。
    assert report.image.path.is_file()

# ---------- 报错要能读：缺字段 / 模板改过 / 号不在 ----------


def test_a_recipe_without_generation_fields_says_which_are_missing(tmp_path):
    cfg = _repo(tmp_path)
    kept(cfg, recipe(prompt="", workflow="", model=""))

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    said = str(caught.value)
    assert "prompt" in said and "workflow" in said and "model" in said
    # 说清楚"这种条目本来就不指望重跑"，而不是只说"失败了"。
    assert "来路" in said


def test_an_id_that_is_not_in_the_library_says_where_to_look(tmp_path):
    cfg = _repo(tmp_path)

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-009", client=RecordingEngine(), today=TODAY)

    assert "fish-009" in str(caught.value)
    assert "gallery" in str(caught.value)


def test_a_sticker_entry_is_not_a_recipe_to_rerun(tmp_path):
    cfg = _repo(tmp_path)
    kept(cfg, recipe(kind="sticker", id="fish-001"))

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert "orig" in str(caught.value)


def test_a_workflow_that_vanished_says_which_file(tmp_path):
    cfg = _repo(tmp_path)
    kept(cfg, recipe(workflow="workflows/gone.json"))

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert "workflows/gone.json" in str(caught.value)


def test_a_workflow_that_lost_a_node_says_which_node(tmp_path):
    import json

    cfg = _repo(tmp_path)
    kept(cfg, recipe())
    path = Path(cfg.repo_root) / "workflows" / "fish-txt2img.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    victim = next(
        key
        for key, node in data.items()
        if (node.get("_meta") or {}).get("title") == "负面词"
    )
    del data[victim]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert "负面词" in str(caught.value)


def test_a_workflow_that_swapped_its_model_says_both_names(tmp_path):
    import json

    cfg = _repo(tmp_path)
    kept(cfg, recipe())
    path = Path(cfg.repo_root) / "workflows" / "fish-txt2img.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for node in data.values():
        inputs = node.get("inputs") or {}
        if "unet_name" in inputs:
            inputs["unet_name"] = "some-other-model.safetensors"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    said = str(caught.value)
    assert "some-other-model.safetensors" in said
    assert "flux-2-klein-base-4b-fp8.safetensors" in said


# ---------- 比不动的时候要说实话 ----------


def test_a_missing_original_is_not_called_a_match(tmp_path):
    cfg = _repo(tmp_path)
    entry = kept(cfg, recipe())
    (Path(cfg.repo_root) / entry.file).unlink()

    report = reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert report.same is None
    assert "不在了" in report.note
    assert report.image.path.is_file()
    # 原图那个号得让出来：账和目录都空了之后号会从 1 重新数，
    # 新图正好落到原图那个位置 —— 那份配方指着的落点就成了一张别的图。
    assert report.image.id == "fish-002"


def test_a_second_run_without_the_original_does_not_compare_against_itself(tmp_path):
    """比不动就说比不动 —— 第二次也一样，不许把上一趟的重跑当成"原图"。"""
    cfg = _repo(tmp_path)
    entry = kept(cfg, recipe())
    (Path(cfg.repo_root) / entry.file).unlink()

    first = reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)
    second = reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert first.same is None and second.same is None
    assert second.image.id != first.image.id


def test_an_unknown_version_is_not_called_a_different_version(tmp_path):
    """配方那栏是「未知」时，说成"版本不一样"会把人引到错的地方去查。"""
    report = _run(tmp_path, recipe_kwargs={"comfyui_version": "未知"})

    assert "版本不一样" not in report.note
    assert "说不准" in report.note


def test_a_recipe_without_steps_says_which_field_is_blank(tmp_path):
    """0 步不是"随便跑跑"，是这一栏当初就没记 —— 报缺字段，别照 0 喂引擎。"""
    cfg = _repo(tmp_path)
    kept(cfg, recipe(steps=0, cfg=0.0))

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    said = str(caught.value)
    assert "steps" in said
    assert "cfg" in said


def test_a_different_engine_version_is_mentioned(tmp_path):
    engine = RecordingEngine(version="0.4.1")
    report = _run(tmp_path, engine=engine)

    assert "0.4.1" in report.note
    assert "0.3.40" in report.note


def test_the_same_engine_version_keeps_quiet(tmp_path):
    report = _run(tmp_path, engine=RecordingEngine(version="0.3.40"))
    assert report.note == ""

# ---------- 配方自带模板，别让 config 的默认值把它顶掉 ----------


def test_the_recipe_owns_the_template_not_the_config(tmp_path):
    cfg = _repo(tmp_path)
    kept(cfg, recipe())
    # 有人把默认模板换成了另一份（真实场景：调新模板调上头了）。
    (Path(cfg.repo_root) / "workflows" / "other.json").write_bytes(
        (REPO_ROOT / "workflows" / "fish-img2img.json").read_bytes()
    )
    tweaked = replace(cfg, workflows=replace(cfg.workflows, default="other.json"))

    report = reproduce(tweaked, "fish-001", client=RecordingEngine(), today=TODAY)

    assert report.image.path.is_file()


# ---------- 底图复用出来的那张，重跑也要拿同一张底图 ----------


def with_base(cfg, *, base_at: str = "output/2026-10-23/before.png") -> Entry:
    """一条 img2img 的配方：这张是拿 before.png 重绘出来的。"""
    entry = recipe(
        workflow="workflows/fish-img2img.json",
        file="output/2026-10-23/fish-001.orig.png",
        source=EntrySource(type="img2img", image=base_at, denoise=0.4),
    )
    kept(cfg, entry)
    base = Path(cfg.repo_root) / base_at
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_bytes(png_bytes())
    return entry


def test_a_base_image_recipe_sends_the_base_image_again(tmp_path):
    cfg = _repo(tmp_path)
    with_base(cfg)
    engine = RecordingEngine()

    reproduce(cfg, "fish-001", client=engine, today=TODAY)

    (sent,) = engine.uploads
    assert sent.name == "before.png", "底图复用那张重跑时要再把同一张底图递进去"
    # 重绘强度也照配方来，不然同一张底图也能跑出另一张图。
    (prompt,) = engine.prompts
    by_title = {
        (node.get("_meta") or {}).get("title"): node
        for node in prompt.values()
        if (node.get("_meta") or {}).get("title")
    }
    assert by_title["重绘强度"]["inputs"]["denoise"] == 0.4


def test_a_missing_base_image_says_which_file(tmp_path):
    cfg = _repo(tmp_path)
    entry = recipe(
        workflow="workflows/fish-img2img.json",
        source=EntrySource(type="img2img", image="output/2026-10-23/gone.png", denoise=0.4),
    )
    kept(cfg, entry)

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert "gone.png" in str(caught.value)

# ---------- 命令行 ----------


def _twin_repo(tmp_path: Path, port: int) -> Path:
    """小仓库：真配置 + 真模板，只有引擎端口指到 stub 上（同 test_pipeline 的做法）。"""
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "config" / "default.toml", repo / "config" / "default.toml")
    shutil.copytree(REPO_ROOT / "workflows", repo / "workflows")
    (repo / "config" / "local.toml").write_text(
        f'[comfy]\nhost = "127.0.0.1"\nport = {port}\n', encoding="utf-8"
    )
    return repo


def test_cli_reproduce_reruns_a_kept_recipe(tmp_path, capsys):
    """票面判据 ①：一条命令重跑就有图；判据 ②：同引擎版本下与原图一致。"""
    from lab.cli import main
    from lab.stub import StubEngine

    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        assert main(["make", "-p", "一条蓝色大肥鱼", "-n", "1", "-s", "100"], repo_root=repo) == 0
        first = next((repo / "output").rglob("fish-001.orig.png"))
        assert main(["keep", str(first)], repo_root=repo) == 0
        capsys.readouterr()
        code = main(["reproduce", "fish-001"], repo_root=repo)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "fish-002" in out
    assert "逐像素一致" in out
    # 判据 ④：重跑的结果自己也在当天那本账里。
    from lab.registry import load_entries, record_path

    entries = load_entries(record_path(repo / "output", date.today()))
    assert [one.id for one in entries] == ["fish-001", "fish-002"]


def test_cli_reproduce_on_an_unknown_id_says_where_to_look(tmp_path, capsys):
    from lab.cli import main
    from lab.stub import StubEngine

    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        code = main(["reproduce", "fish-042"], repo_root=repo)

    err = capsys.readouterr().err
    assert code == 2, err
    assert "fish-042" in err
    assert "gallery" in err

def test_an_imported_entry_has_no_recipe_to_rerun(tmp_path):
    """外来的那张记的是图本身，不是出它的那次生成 —— 说清楚，别只说"没有"。"""
    cfg = _repo(tmp_path)
    kept(cfg, recipe(kind="imported", prompt="", workflow="", model="", seed=0))

    with pytest.raises(ValueError) as caught:
        reproduce(cfg, "fish-001", client=RecordingEngine(), today=TODAY)

    assert "imported" in str(caught.value)
    assert "来路" in str(caught.value)

def test_a_rerun_that_differs_still_exits_zero(tmp_path, capsys):
    """不一样也是跑完了 —— 退出码只区分"跑成 / 没跑成"，不替人判断好坏。"""
    from lab.cli import main
    from lab.stub import StubEngine

    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        assert main(["make", "-p", "x", "-n", "1", "-s", "7"], repo_root=repo) == 0
        first = next((repo / "output").rglob("fish-001.orig.png"))
        assert main(["keep", str(first)], repo_root=repo) == 0
        # 原图换成另一张画 —— 重跑当然对不上。
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (8, 8), (200, 10, 10)).save(buffer, format="PNG")
        first.write_bytes(buffer.getvalue())
        capsys.readouterr()
        code = main(["reproduce", "fish-001"], repo_root=repo)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "不一样" in out
    assert "逐像素一致" not in out
