"""出片管线的行为：一句话进去，一批图出来。

这里的分层很清楚：
- **对着 stub 引擎**跑一条真的端到端（子进程、真命令、真搬文件），证明
  "无显卡也能跑同一条命令"不是口号；
- **对着记录型假引擎**跑参数注入，因为要验的是"交给引擎的话对不对"，
  这种情况下把 prompt 捞出来看才是真正的行为断言。

假引擎只在 `_run` 的 client 接缝上出现 —— 不是 mock 掉被测代码，
是替换掉那个本来就该被替换的外部物（和 test_comfy 对 stub 的态度一致）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from lab.comfy import GeneratedImage, JobResult
from lab.config import load_config
from lab.pipeline import (
    MAX_SEED,
    SilentProgress,
    make,
    random_seed,
)
from lab.stub import StubEngine
from lab.workflows import GenSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / "workflows" / "fish-txt2img.json"


def png_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def spec(**overrides) -> GenSpec:
    base = GenSpec(
        prompt="chibi blue whale girl",
        negative="",
        seed=12345,
        steps=20,
        cfg=5.0,
        width=1024,
        height=1024,
    )
    return GenSpec(**{**base.__dict__, **overrides})


class RecordingEngine:
    """记录每次提交的 prompt，其余照 EngineClient 的样子说话。

    它不假装懂工作流 —— 记下来的 prompt 是给人看的证据，图是固定的一张真 PNG。
    """

    def __init__(self) -> None:
        self.prompts: list[dict] = []
        self.waits: list[str] = []
        self.fetches: list[str] = []
        self.uploads: list[Path] = []
        self.upload_names: list[str] = []
        self._counter = 0

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        _ = client_id
        self.prompts.append(prompt)
        self._counter += 1
        return f"job-{self._counter}"

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        self.waits.append(prompt_id)
        if on_poll is not None:
            on_poll(1, 0.5)  # 让上层报一次"还在等"
        image = GeneratedImage(
            filename="fish/txt2img_00001_.png", subfolder="", kind="output", node="13"
        )
        return JobResult(prompt_id=prompt_id, images=(image,), files=(), history={})

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        self.fetches.append(image.filename)
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(image.filename).name
        target.write_bytes(png_bytes())
        return target

    def upload_image(self, path: Path, name: str | None = None) -> str:
        """记录底图上传 —— 真上传在 test_comfy 里对着 stub 走一遍 HTTP。"""
        self.uploads.append(Path(path))
        resolved = name or Path(path).name
        self.upload_names.append(resolved)
        return resolved


class BreaksOnSecond(RecordingEngine):
    """第一张正常出，第二张当场炸 —— 用来验"已经出成的那张不会跟着丢"。"""

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        if self._counter >= 1:
            raise RuntimeError("引擎在第 2 张上炸了")
        return super().submit(prompt, client_id)


class BatchedEngine(RecordingEngine):
    """一次提交回来 N 张图的引擎 —— 模拟模板里把 batch_size 改大。"""

    def __init__(self, copies: int = 4) -> None:
        super().__init__()
        self.copies = copies

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        result = super().wait(prompt_id, on_poll)
        images = tuple(
            GeneratedImage(
                filename=f"fish/txt2img_0000{n}_.png",
                subfolder="",
                kind="output",
                node="13",
            )
            for n in range(1, self.copies + 1)
        )
        return JobResult(
            prompt_id=result.prompt_id, images=images, files=(), history={}
        )


def _run(tmp_path: Path, engine=None, spec_kwargs=None, **kwargs):
    """在 tmp 里当一个完整的小仓库跑一遍：output/ 与 workflows/ 都在这儿。

    仓库根也挪到 tmp，是为了让记录里的 file 真的相对仓库根 ——
    留在真仓库而只换 output_dir 的话，记录写出来会是绝对路径。
    """
    from dataclasses import replace

    template_dir = tmp_path / "workflows"
    if not template_dir.exists():
        template_dir.parent.mkdir(parents=True, exist_ok=True)
        template_dir.symlink_to(REPO_ROOT / "workflows")

    cfg = load_config(REPO_ROOT)
    cfg = replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
    )
    return make(
        cfg,
        spec(**(spec_kwargs or {})),
        client=engine or RecordingEngine(),
        progress=SilentProgress(),
        today=date(2026, 10, 23),
        **kwargs,
    )


# ---------- 命名与落盘 ----------


def test_make_writes_count_images_into_a_daily_folder(tmp_path):
    made = _run(tmp_path, count=4)

    assert len(made) == 4
    folder = tmp_path / "output" / "2026-10-23"
    assert {p.path.parent for p in made} == {folder}
    assert all(p.path.exists() for p in made)


def test_names_follow_fish_serial_dot_kind(tmp_path):
    made = _run(tmp_path, count=2)

    assert [p.path.name for p in made] == ["fish-001.orig.png", "fish-002.orig.png"]


def test_serials_continue_after_existing_files(tmp_path):
    folder = tmp_path / "output" / "2026-10-23"
    folder.mkdir(parents=True)
    (folder / "fish-007.orig.png").write_bytes(b"x")

    made = _run(tmp_path, count=2)

    assert [p.path.name for p in made] == ["fish-008.orig.png", "fish-009.orig.png"]


# ---------- 全量记录（票 #11） ----------


def records(tmp_path: Path):
    from lab.registry import load_entries, record_path

    return load_entries(record_path(tmp_path / "output", date(2026, 10, 23)))


def test_every_image_of_the_day_lands_in_the_record(tmp_path):
    """验收判据 1：出完当天就能查到每张的种子与完整参数。"""
    made = _run(tmp_path, count=3)

    logged = records(tmp_path)
    assert [one.id for one in logged] == ["fish-001", "fish-002", "fish-003"]
    assert [one.seed for one in logged] == [one.spec.seed for one in made]
    first = logged[0]
    assert (first.kind, first.steps, first.cfg) == ("orig", 20, 5.0)
    assert first.prompt == "chibi blue whale girl"


def test_the_record_points_at_the_picture_it_describes(tmp_path):
    """记录里存的是相对仓库根的路径 —— 仓库挪个位置，账还读得懂。"""
    _run(tmp_path, count=1)

    (only,) = records(tmp_path)
    assert only.file.endswith("2026-10-23/fish-001.orig.png")
    assert not Path(only.file).is_absolute()


def test_the_record_sizes_the_picture(tmp_path):
    """宽高从图里量出来 —— 站上要用，回执里也省得再开一次图。"""
    _run(tmp_path, count=1)

    (only,) = records(tmp_path)
    assert (only.width, only.height) == (8, 8)


def test_the_record_keeps_the_size_that_was_asked_for(tmp_path):
    """请求尺寸单独记一笔：成品量出来的是引擎给的结果，未必等于喂进去的数。

    假引擎回的永远是 8×8，而 spec 要的是 1024 —— 两个数都得在账上，
    否则重跑时只能照着成品的尺寸喂，等于把当时的请求改掉了。
    """
    _run(tmp_path, count=1)

    (only,) = records(tmp_path)
    assert (only.requested_width, only.requested_height) == (1024, 1024)
    assert (only.width, only.height) == (8, 8)


def test_the_record_names_the_template_that_made_it(tmp_path):
    _run(tmp_path, count=1)

    (only,) = records(tmp_path)
    assert only.workflow == "workflows/fish-txt2img.json"
    assert only.model == "flux-2-klein-base-4b-fp8.safetensors"


def test_the_record_carries_the_base_image_and_the_denoise(tmp_path):
    """有底图的那批，重绘强度得跟着记录走 —— 它是那张图上唯一的旋钮。"""
    init = base_image(tmp_path)
    _run(tmp_path, count=1, spec_kwargs={"init_image": init, "denoise": 0.4})

    (only,) = records(tmp_path)
    assert only.source is not None
    assert only.source.denoise == 0.4
    assert only.source.image == init.relative_to(tmp_path).as_posix()


def test_a_pure_text_run_records_no_source(tmp_path):
    _run(tmp_path, count=1)

    (only,) = records(tmp_path)
    assert only.source is None


def test_a_second_batch_the_same_day_does_not_reuse_a_serial(tmp_path):
    """验收判据 6：同一天多批生成，编号不冲突。

    中间把图清走了（废图就是这么处理的）—— 光看目录会从 001 重来，
    两条不同的生成顶着同一个 id，事后不知道该信哪一条。
    """
    _run(tmp_path, count=2)
    folder = tmp_path / "output" / "2026-10-23"
    for path in folder.glob("fish-*.png"):
        path.unlink()

    _run(tmp_path, count=2)

    assert [one.id for one in records(tmp_path)] == [
        "fish-001",
        "fish-002",
        "fish-003",
        "fish-004",
    ]


def test_a_failed_batch_keeps_what_already_came_out(tmp_path):
    """第 2 张炸了，第 1 张的记录不能跟着丢 —— 已经出成的图本来就留着了。"""
    with pytest.raises(RuntimeError):
        _run(tmp_path, count=2, engine=BreaksOnSecond())

    assert [one.id for one in records(tmp_path)] == ["fish-001"]


# ---------- 搬运，不是引用 ----------


def test_images_are_moved_out_of_the_engine_staging_area(tmp_path):
    """引擎的 output 迟早会被清掉 —— 所以图必须是搬过来的，staging 得是空的。"""
    engine = RecordingEngine()
    made = _run(tmp_path, engine=engine, count=1)

    assert engine.fetches == ["fish/txt2img_00001_.png"]  # 引擎侧的名字只在这出现
    assert made[0].engine_filename == "fish/txt2img_00001_.png"
    assert made[0].path.read_bytes() == png_bytes()  # 字节真的过来了


def test_engine_filename_is_not_reused_as_the_output_name(tmp_path):
    """引擎回的名字（txt2img_00001_）不能直接当成品名。"""
    made = _run(tmp_path, count=1)

    assert made[0].path.name != "txt2img_00001_.png"


# ---------- 种子 ----------


def test_each_image_gets_its_own_seed(tmp_path):
    made = _run(tmp_path, count=3, seed=1000)

    assert [p.spec.seed for p in made] == [1000, 1001, 1002]


def test_seed_is_written_into_the_prompt_it_sent(tmp_path):
    engine = RecordingEngine()
    _run(tmp_path, engine=engine, count=2, seed=777)

    seeds = [
        node["inputs"]["noise_seed"]
        for prompt in engine.prompts
        for node in prompt.values()
        if (node.get("_meta") or {}).get("title") == "种子"
    ]
    assert seeds == [777, 778]


def test_same_seed_gives_the_same_batch_again(tmp_path):
    """同种子同参数 = 同一批种子序列，这样单张也能重跑。"""
    first = _run(tmp_path / "a", count=3, seed=42)
    second = _run(tmp_path / "b", count=3, seed=42)

    assert [p.spec.seed for p in first] == [p.spec.seed for p in second]


def test_seed_wraps_instead_of_overflowing(tmp_path):
    made = _run(tmp_path, count=2, seed=MAX_SEED)

    assert [p.spec.seed for p in made] == [MAX_SEED, 0]


def test_random_seed_stays_in_range():
    for _ in range(50):
        assert 0 <= random_seed() <= MAX_SEED


# ---------- 参数注入 ----------


def test_business_values_land_in_the_title_nodes(tmp_path):
    engine = RecordingEngine()
    _run(
        tmp_path,
        engine=engine,
        count=1,
        seed=5,
    )

    prompt = engine.prompts[0]
    by_title = {
        (node.get("_meta") or {}).get("title"): node
        for node in prompt.values()
        if (node.get("_meta") or {}).get("title")
    }
    assert by_title["提示词"]["inputs"]["text"] == "chibi blue whale girl"
    assert by_title["负面词"]["inputs"]["text"] == ""
    assert by_title["种子"]["inputs"]["noise_seed"] == 5
    assert by_title["步数"]["inputs"]["steps"] == 20
    assert by_title["引导强度"]["inputs"]["cfg"] == 5.0
    assert by_title["尺寸"]["inputs"]["width"] == 1024


def test_injection_survives_node_ids_being_reshuffled(tmp_path):
    """重排工作流会换掉全部节点 ID，注入靠标题所以必须仍然生效。"""
    reshuffled = {}
    for position, (node_id, node) in enumerate(
        json.loads(WORKFLOW.read_text(encoding="utf-8")).items(), start=101
    ):
        _ = node_id
        reshuffled[str(position)] = node

    engine = RecordingEngine()
    made = _run(tmp_path, engine=engine, count=1, seed=9)

    # 拿重排后的模板再跑一次：pipeline 读的是 workflows/ 下的文件，所以这里直接比 prompt
    from lab.workflows import apply

    applied = apply(reshuffled, spec(seed=9))
    injected = {
        (node.get("_meta") or {}).get("title"): node["inputs"].get("noise_seed")
        for node in applied.values()
        if (node.get("_meta") or {}).get("title") == "种子"
    }
    assert injected["种子"] == 9
    assert engine.prompts  # 原来的路径也照常工作
    assert made


def test_size_is_consistent_across_every_node_that_has_one(tmp_path):
    engine = RecordingEngine()
    _run(tmp_path, engine=engine, count=1)
    engine.prompts.clear()
    _run(
        tmp_path,
        engine=engine,
        count=1,
        spec_kwargs={"width": 768, "height": 512},
    )

    prompt = engine.prompts[0]
    sized = [
        node["inputs"]
        for node in prompt.values()
        if "width" in node.get("inputs", {}) and "height" in node.get("inputs", {})
    ]
    assert sized, "模板里应该至少有一个带尺寸的节点"
    assert all(s["width"] == 768 and s["height"] == 512 for s in sized)


# ---------- 进度 ----------


class RecordingProgress:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def start(self, index: int, total: int, seed: int) -> None:
        self.events.append(("start", index, total, seed))

    def waiting(self, index: int, total: int, elapsed: float) -> None:
        self.events.append(("waiting", index, total))

    def done(self, index: int, total: int, path: Path, seed: int) -> None:
        self.events.append(("done", index, total, seed))


def test_progress_speaks_once_per_image(tmp_path):
    progress = RecordingProgress()
    cfg = load_config(REPO_ROOT)
    from dataclasses import replace

    cfg = replace(cfg, paths=replace(cfg.paths, output_dir=tmp_path / "output"))
    make(
        cfg,
        spec(),
        count=2,
        client=RecordingEngine(),
        progress=progress,
        today=date(2026, 10, 23),
    )

    kinds = [event[0] for event in progress.events]
    assert kinds.count("start") == 2
    assert kinds.count("done") == 2
    assert kinds.count("waiting") == 2  # 假引擎每次 wait 报一次


# ---------- 出错 ----------


def test_count_below_one_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _run(tmp_path, count=0)


def test_a_batched_template_is_refused_instead_of_silently_dropped(tmp_path):
    """模板 batch_size 被改成 N 时，一次提交回来 N 张同种子的图。

    它们没法单独重跑，所以宁可当场报错 —— 白算几分钟再悄悄丢掉 N-1 张更糟。
    """
    made_before = sorted((tmp_path / "output").rglob("*.png")) if (tmp_path / "output").is_dir() else []
    assert made_before == []

    with pytest.raises(ValueError, match="batch_size"):
        _run(tmp_path, count=1, engine=BatchedEngine(copies=4))

    assert sorted((tmp_path / "output").rglob("*.png")) == []


# ---------- 对着 stub 引擎真跑 ----------


def test_make_runs_against_the_stub_engine(tmp_path):
    """无显卡也能端到端：同一句命令，对着 stub 出声。"""
    from dataclasses import replace

    cfg = load_config(REPO_ROOT)
    cfg = replace(cfg, paths=replace(cfg.paths, output_dir=tmp_path / "output"))

    with StubEngine() as engine:
        from lab.comfy import EngineClient

        client = EngineClient(
            engine.host, engine.port, poll_interval=0.01, max_polls=50
        )
        made = make(
            cfg,
            spec(),
            count=2,
            client=client,
            progress=SilentProgress(),
            today=date(2026, 10, 23),
        )

    assert [p.path.name for p in made] == ["fish-001.orig.png", "fish-002.orig.png"]
    assert all(p.path.read_bytes() == engine.image for p in made)


# ---------- 命令行 ----------


def _twin_repo(tmp_path: Path, port: int) -> Path:
    """造一个小仓库：真的配置 + 真的工作流，只有引擎端口指到 stub 上。

    这样命令走的是同一条路径（argparse → 管线 → 引擎客户端 → HTTP），
    唯一的替换是"引擎在哪" —— 也就是 stub 存在的意义。
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


def test_cli_make_prints_each_path_and_seed(tmp_path, capsys):
    """票面的验收标准：终端打印每张的路径与种子。"""
    from lab.cli import main

    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        code = main(
            ["make", "--prompt", "一条蓝色大肥鱼", "--count", "2", "--seed", "100"],
            repo_root=repo,
        )

    out = capsys.readouterr().out
    assert code == 0, out
    assert "fish-001.orig.png" in out
    assert "fish-002.orig.png" in out
    assert "seed=100" in out
    assert "seed=101" in out


def test_cli_make_creates_the_daily_folder(tmp_path, capsys):
    from lab.cli import main

    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        main(["make", "-p", "x", "-n", "1"], repo_root=repo)

    capsys.readouterr()
    folders = [p for p in (repo / "output").iterdir() if p.is_dir()]
    assert len(folders) == 1
    assert folders[0].name == date.today().isoformat()
    assert [f.name for f in folders[0].glob("*.png")] == ["fish-001.orig.png"]


def test_cli_make_needs_a_prompt(tmp_path, capsys):
    """没给 --prompt 就当用错命令（退出码 2），不是默默画点什么。"""
    from lab.cli import main

    with pytest.raises(SystemExit) as caught:
        main(["make"], repo_root=_twin_repo(tmp_path, 1))

    assert caught.value.code == 2
    assert "--prompt" in capsys.readouterr().err


def test_cli_make_refuses_an_empty_prompt(tmp_path, capsys):
    from lab.cli import main

    code = main(["make", "-p", "   "], repo_root=_twin_repo(tmp_path, 1))

    assert code == 2
    assert "画什么" in capsys.readouterr().err


# ---------- 底图复用（票 #8） ----------


def base_image(tmp_path: Path, name: str = "base.png") -> Path:
    """一张真的 PNG 当底图 —— 上传那段要读它的字节。"""
    path = tmp_path / name
    path.write_bytes(png_bytes())
    return path


def _node_by_title(prompt: dict, title: str) -> dict:
    from lab.workflows import find_by_title

    node_id = find_by_title(prompt, title)
    assert node_id is not None, f"交出去的 prompt 里没有标题「{title}」"
    return prompt[node_id]


def _classes(prompt: dict) -> set[str]:
    return {node["class_type"] for node in prompt.values()}


def test_a_base_image_switches_to_the_img2img_template(tmp_path):
    """验收判据 1：给了底图就走另一个模板。"""
    engine = RecordingEngine()
    init = base_image(tmp_path)

    _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": init})

    classes = _classes(engine.prompts[0])
    assert "LoadImage" in classes
    assert "VAEEncode" in classes
    assert "EmptyFlux2LatentImage" not in classes


def test_without_a_base_image_nothing_changes(tmp_path):
    """验收判据 5：不给底图时，走的就是原来那条路，也不碰引擎的 input 目录。"""
    engine = RecordingEngine()

    _run(tmp_path, engine=engine, count=1)

    classes = _classes(engine.prompts[0])
    assert "EmptyFlux2LatentImage" in classes
    assert "LoadImage" not in classes
    assert engine.uploads == []


def test_the_base_image_is_uploaded_once_for_the_whole_batch(tmp_path):
    engine = RecordingEngine()
    init = base_image(tmp_path)

    _run(tmp_path, engine=engine, count=3, spec_kwargs={"init_image": init})

    assert engine.uploads == [init]
    assert len(engine.prompts) == 3


def test_the_uploaded_name_lands_in_the_template(tmp_path):
    engine = RecordingEngine()
    init = base_image(tmp_path)

    _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": init})

    loader = _node_by_title(engine.prompts[0], "底图")
    assert loader["class_type"] == "LoadImage"
    assert loader["inputs"]["image"] == engine.upload_names[0]
    # 名字得是安全的 ASCII：素材路径里带中文和空格，引擎的 input 目录不该沾上
    assert engine.upload_names[0].startswith("lab-init-")
    assert engine.upload_names[0].endswith(".png")


def test_the_same_picture_uploads_under_the_same_name(tmp_path):
    """同内容的底图两次跑用同一个名字 —— 上传才是幂等的，不会在引擎里堆垃圾。"""
    engine = RecordingEngine()
    first = base_image(tmp_path, "one.png")
    second = tmp_path / "copy.png"
    second.write_bytes(first.read_bytes())

    _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": first})
    _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": second})

    assert engine.upload_names[0] == engine.upload_names[1]


def test_denoise_lands_on_the_split_node(tmp_path):
    """验收判据 2：重绘强度可调。"""
    engine = RecordingEngine()
    init = base_image(tmp_path)

    _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": init, "denoise": 0.3})

    node = _node_by_title(engine.prompts[0], "重绘强度")
    assert node["class_type"] == "SplitSigmasDenoise"
    assert node["inputs"]["denoise"] == 0.3


def test_the_record_keeps_the_base_image_and_the_denoise(tmp_path):
    """验收判据 3：底图与重绘强度进登记信息，形象跑掉时知道从哪退。"""
    init = base_image(tmp_path)

    made = _run(tmp_path, count=1, spec_kwargs={"init_image": init, "denoise": 0.4})

    source = made[0].source
    assert source is not None
    assert source.type == "img2img"  # design.md §6.2 的 source 形状
    assert source.image == init
    assert source.denoise == 0.4
    assert source.engine_filename.endswith(".png")


def test_a_pure_text_run_has_no_source(tmp_path):
    """文生图的记录里不该凭空多出一个「底图」。"""
    made = _run(tmp_path, count=1)

    assert made[0].source is None


def test_a_missing_base_image_is_a_readable_error(tmp_path):
    """验收判据 4：底图不在，说清楚是哪个文件不在。"""
    missing = tmp_path / "nope.png"

    with pytest.raises(ValueError) as caught:
        _run(tmp_path, count=1, spec_kwargs={"init_image": missing})

    message = str(caught.value)
    assert "底图" in message
    assert str(missing) in message


def test_an_unsupported_base_image_format_is_a_readable_error(tmp_path):
    """验收判据 4：格式不对，把支持哪些格式说出来。"""
    bad = tmp_path / "base.txt"
    bad.write_text("not an image", encoding="utf-8")

    with pytest.raises(ValueError) as caught:
        _run(tmp_path, count=1, spec_kwargs={"init_image": bad})

    message = str(caught.value)
    assert "底图" in message
    assert str(bad) in message
    assert ".png" in message


def test_a_missing_base_image_is_caught_before_the_engine_is_touched(tmp_path):
    """校验发生在提交之前 —— 底图写错时不该先白交一份任务。"""
    engine = RecordingEngine()

    with pytest.raises(ValueError):
        _run(tmp_path, engine=engine, count=1, spec_kwargs={"init_image": tmp_path / "nope.png"})

    assert engine.prompts == []
    assert engine.uploads == []


@pytest.mark.parametrize("denoise", [0.0, -0.1, 1.5])
def test_an_out_of_range_denoise_is_refused(denoise, tmp_path):
    """重绘强度是这条路上唯一的旋钮，范围外没有意义 —— 当场说清楚，别白算一批。"""
    engine = RecordingEngine()
    init = base_image(tmp_path)

    with pytest.raises(ValueError) as caught:
        _run(
            tmp_path,
            engine=engine,
            count=1,
            spec_kwargs={"init_image": init, "denoise": denoise},
        )

    assert "重绘强度" in str(caught.value)
    assert engine.prompts == []


def test_cli_make_with_a_base_image_goes_through_the_stub(tmp_path, capsys):
    """整条命令走一遍：argparse → 管线 → 上传 → 提交 → 搬运。"""
    from lab.cli import main

    init = base_image(tmp_path)
    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        code = main(
            [
                "make",
                "-p",
                "同一只鲸鱼娘，换个表情",
                "-n",
                "1",
                "--init-image",
                str(init),
                "--denoise",
                "0.4",
            ],
            repo_root=repo,
        )
        uploaded = dict(engine.uploads)
        loader = _node_by_title(engine.prompts[-1], "底图")

    out = capsys.readouterr().out
    assert code == 0, out
    assert "fish-001.orig.png" in out
    assert str(init) in out
    assert "0.4" in out
    assert list(uploaded.values()) == [init.read_bytes()]
    # 交出去的模板里写的必须是上传后的那个名字 —— stub 收到的键就是它
    assert loader["inputs"]["image"] in uploaded


def test_cli_make_reports_a_missing_base_image(tmp_path, capsys):
    from lab.cli import main

    code = main(
        ["make", "-p", "x", "--init-image", str(tmp_path / "nope.png")],
        repo_root=_twin_repo(tmp_path, 1),
    )

    assert code == 2
    assert "底图" in capsys.readouterr().err


def test_cli_refuses_denoise_without_a_base_image(tmp_path, capsys):
    """没有底图就没有重绘 —— 给了 --denoise 却不给 --init-image，当场说清楚。"""
    from lab.cli import main

    code = main(
        ["make", "-p", "x", "--denoise", "0.4"],
        repo_root=_twin_repo(tmp_path, 1),
    )

    assert code == 2
    assert "--init-image" in capsys.readouterr().err


def test_cli_workflow_flag_swaps_the_template_in_use(tmp_path, capsys):
    """--workflow 换的是「这次会用到的那一份」：有底图就换 img2img 那份。"""
    from lab.cli import main

    init = base_image(tmp_path)
    with StubEngine() as engine:
        repo = _twin_repo(tmp_path, engine.port)
        source = repo / "workflows" / "fish-img2img.json"
        (repo / "workflows" / "custom.json").write_text(
            source.read_text(encoding="utf-8"), encoding="utf-8"
        )
        code = main(
            [
                "make",
                "-p",
                "x",
                "-n",
                "1",
                "--init-image",
                str(init),
                "--workflow",
                "custom.json",
            ],
            repo_root=repo,
        )
        classes = _classes(engine.prompts[-1])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "LoadImage" in classes  # 走的是 --workflow 指的那份 img2img 模板
