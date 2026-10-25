"""出片台的接口层（M5.2）：出片、去背、保留、打开文件夹、引擎状态，真起一遍服务。

两条接缝故意留成可注入的：`client`（引擎）与 `opener`（打开文件夹）。前者让
"页面提交的 prompt"与命令行提交的可以逐字节比（判据 ②），后者让"打开文件夹"这件
事在测试里不真去弹文件管理器。别的都走真 HTTP —— 判据 ① 说的是"页面上点得动"，
只断言路由表证明不了这件事。
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import Any

import uvicorn

from lab.comfy import EngineUnreachable, GeneratedImage, JobResult
from lab.config import load_config
from lab.pipeline import spec_for
from lab.stub import StubEngine
from lab.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]


def png_bytes(mode: str = "RGB") -> bytes:
    from PIL import Image, ImageDraw

    if mode == "RGBA":
        image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
        ImageDraw.Draw(image).rectangle((4, 4, 11, 11), fill=(30, 120, 220, 255))
    else:
        image = Image.new("RGB", (16, 16), (10, 20, 30))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TickingEngine:
    """假引擎：提交即收、随手报一次"还在等"、回一张真 PNG。

    比 tests/test_jobs.py 那件多两个方法 —— 页面还要问引擎"在不在、排了多少"。
    """

    def __init__(self, *, running: int = 1, pending: int = 2, image: bytes | None = None):
        self.prompts: list[dict] = []
        self.uploads: list[Path] = []
        self.upload_names: list[str] = []
        self.running = running
        self.pending = pending
        self.image = image if image is not None else png_bytes()
        self.count = 0

    def version(self) -> str:
        return "0.42.0"

    def queue(self) -> tuple[int, int]:
        return self.running, self.pending

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        _ = client_id
        self.prompts.append(prompt)
        self.count += 1
        return f"job-{self.count}"

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        if on_poll is not None:
            on_poll(1, 0.2)
        image = GeneratedImage(
            filename=f"fish/txt2img_{self.count:05d}_.png",
            subfolder="",
            kind="output",
            node="13",
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


class DeadEngine:
    """连不上的引擎：状态条该说清楚怎么回事，而不是假装 0。

    六个方法都实现，因为页面要的引擎是出片那四个 + 状态条那两个的并集
    （lab.web.WebEngine）。只实现后两个的话，这个替身就不是「连不上的引擎」，
    而是「一个出了状态条以外什么都不会的引擎」—— 那是另一件事。
    """

    DOWN = "连不上 127.0.0.1:8188"

    def version(self) -> str:
        raise EngineUnreachable(self.DOWN)

    def queue(self) -> tuple[int, int]:
        raise EngineUnreachable(self.DOWN)

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        raise EngineUnreachable(self.DOWN)

    def wait(
        self, prompt_id: str, on_poll: Callable[[int, float], None] | None = None
    ) -> JobResult:
        raise EngineUnreachable(self.DOWN)

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        raise EngineUnreachable(self.DOWN)

    def upload_image(self, path: Path, name: str | None = None) -> str:
        raise EngineUnreachable(self.DOWN)


class RecordingOpener:
    """记下被要求打开的那些文件夹，不真去弹窗口。"""

    def __init__(self, ok: bool = True) -> None:
        self.folders: list[Path] = []
        self.ok = ok

    def __call__(self, folder: Path) -> bool:
        self.folders.append(Path(folder))
        return self.ok


def _cfg(tmp_path: Path):
    cfg = load_config(REPO_ROOT)
    workflows = tmp_path / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    for name in ("fish-txt2img.json", "fish-img2img.json", "fish-sticker.json"):
        (workflows / name).write_text(
            (REPO_ROOT / "workflows" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
    )


def _picture(tmp_path: Path, name: str = "2026-10-23/fish-001.orig.png") -> Path:
    """在当天那个文件夹里放一张真 PNG，当成"已经出过的那张"。"""
    path = tmp_path / "output" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png_bytes())
    return path


@contextmanager
def _serving(app) -> Iterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise AssertionError("本地界面没能在 15 秒内起来")
    assert server.servers, "服务起来了但没监听"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def _post(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw}


def _get(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _events(url: str, *, stop: str, limit: int = 40) -> list[dict]:
    """把 SSE 读成事件列表，读到 stop 这种事件（或读到 limit 条）为止。"""
    events: list[dict] = []
    with urllib.request.urlopen(url, timeout=20) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line[len("data:") :].strip())
            events.append(event)
            if event.get("type") == stop or len(events) >= limit:
                break
    return events


# ---------- 引擎状态条 ----------


def test_engine_state_says_how_the_engine_is_doing(tmp_path):
    engine = TickingEngine(running=1, pending=2)
    with _serving(create_app(_cfg(tmp_path), client=engine)) as base:
        status, raw = _get(base + "/api/engine")
    body = json.loads(raw)
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == "0.42.0"
    assert (body["running"], body["pending"]) == (1, 2)


def test_engine_state_says_why_when_the_engine_is_down(tmp_path):
    with _serving(create_app(_cfg(tmp_path), client=DeadEngine())) as base:
        status, raw = _get(base + "/api/engine")
    body = json.loads(raw)
    assert status == 200, "连不上引擎不是服务的错 —— 状态条要能看到原因"
    assert body["ok"] is False
    assert "连不上" in body["message"]


def test_engine_events_push_the_same_thing_without_the_page_asking(tmp_path):
    engine = TickingEngine(running=0, pending=3)
    with _serving(create_app(_cfg(tmp_path), client=engine)) as base:
        events = _events(base + "/api/engine/events", stop="engine")
    assert events and events[0]["type"] == "engine"
    assert events[0]["ok"] is True
    assert events[0]["pending"] == 3


# ---------- 出片 ----------


def test_make_wants_a_prompt(tmp_path):
    engine = TickingEngine()
    with _serving(create_app(_cfg(tmp_path), client=engine)) as base:
        status, body = _post(base + "/api/make", {"prompt": "   ", "count": 2})
    assert status == 400
    assert "画点什么" in body["message"]
    assert engine.prompts == [], "话都没说清楚，不该去打扰引擎"


def test_make_runs_the_batch_and_tells_the_page_each_step(tmp_path):
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    with _serving(create_app(cfg, client=engine)) as base:
        status, body = _post(base + "/api/make", {"prompt": "chibi blue whale girl", "count": 2})
        assert status == 200, body
        events = _events(base + f"/api/jobs/{body['job']}/events", stop="finished")
    kinds = [event["type"] for event in events]
    assert kinds == ["spec", "start", "done", "start", "done", "finished"], kinds
    assert events[0]["prompt"] == "chibi blue whale girl"
    assert events[0]["base"] == "", "纯文生图没有底图"
    assert events[0]["denoise"] is None
    assert events[-1]["made"] == 2
    folder = cfg.paths.output_dir / events[-1]["folder"]
    assert sorted(path.name for path in folder.glob("*.orig.png")) == [
        "fish-001.orig.png",
        "fish-002.orig.png",
    ]
    assert (folder / "registry.local.json").is_file(), "一张一落账，账要跟着出来"


def test_a_job_says_which_recipe_its_pictures_came_from(tmp_path):
    """卡片上的「重跑这张」要照它自己那份参数 —— 头一条得先把那份参数说清楚。"""
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with _serving(create_app(cfg, client=engine)) as base:
        status, body = _post(
            base + "/api/make",
            {
                "prompt": "same girl, another mood",
                "count": 1,
                "init_image": "2026-10-23/fish-001.orig.png",
                "denoise": 0.4,
            },
        )
        assert status == 200, body
        events = _events(base + f"/api/jobs/{body['job']}/events", stop="finished")
    spec = events[0]
    assert spec["type"] == "spec"
    assert spec["prompt"] == "same girl, another mood"
    assert spec["base"] == "2026-10-23/fish-001.orig.png"
    assert spec["denoise"] == 0.4


def test_a_batch_size_left_empty_falls_back_to_the_config(tmp_path):
    """一次几张只有一处来源：页面不说，就按 config 的 [generate].count 走。"""
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    with _serving(create_app(cfg, client=engine)) as base:
        status, body = _post(base + "/api/make", {"prompt": "chibi blue whale girl"})
        assert status == 200, body
        events = _events(base + f"/api/jobs/{body['job']}/events", stop="finished")
    assert len(engine.prompts) == cfg.generate.count
    assert events[-1]["made"] == cfg.generate.count
    assert events[0]["type"] == "spec"


def test_a_job_that_is_not_there_is_a_404(tmp_path):
    with _serving(create_app(_cfg(tmp_path), client=TickingEngine())) as base:
        status, _ = _get(base + "/api/jobs/nope/events")
    assert status == 404


def test_the_page_and_the_command_line_send_the_same_prompt(tmp_path):
    """判据 ②：页面设的参数与命令行同参数，交出去的应当是同一份话。

    两边都从 pipeline.spec_for 过 —— 命令行那条路由 tests/test_pipeline.py 守着。
    这里比的是逐字节相同：同一份 spec 交出去的 prompt 一模一样，同 seed 出同样的图。
    """
    from lab.workflows import apply, load

    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    # 这个字典要原样喂给 spec_for 的具名参数，各参数类型不同（str / int / float）——
    # 不标 Any 的话 mypy 只能把它当 dict[str, object]，展开处逐参数报一遍。
    knobs: dict[str, Any] = {
        "prompt": "chibi blue whale girl",
        "negative": "blurry",
        "seed": 12345,
        "steps": 30,
        "cfg": 4.0,
        "width": 768,
        "height": 768,
    }
    with _serving(create_app(cfg, client=engine)) as base:
        status, _ = _post(base + "/api/make", {**knobs, "count": 1})
        assert status == 200
    mine = spec_for(cfg, **knobs)
    theirs = apply(load(Path(cfg.repo_root) / "workflows" / cfg.workflows.default), mine)
    assert json.dumps(engine.prompts[0], sort_keys=True) == json.dumps(
        theirs, sort_keys=True
    )


def test_a_base_image_is_uploaded_and_the_slider_gets_through(tmp_path):
    from lab.workflows import TITLE_DENOISE, find_by_title

    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with _serving(create_app(cfg, client=engine)) as base:
        status, body = _post(
            base + "/api/make",
            {
                "prompt": "same girl, another mood",
                "count": 1,
                "init_image": "2026-10-23/fish-001.orig.png",
                "denoise": 0.4,
            },
        )
        assert status == 200, body
        _events(base + f"/api/jobs/{body['job']}/events", stop="finished")
    assert engine.uploads == [cfg.paths.output_dir.resolve() / "2026-10-23" / "fish-001.orig.png"]
    assert engine.upload_names[0].startswith("lab-init-"), "上传名由 pipeline 统一给"
    node = engine.prompts[0][find_by_title(engine.prompts[0], TITLE_DENOISE)]
    assert node["inputs"]["denoise"] == 0.4


def test_a_base_image_from_outside_the_output_folder_is_refused(tmp_path):
    engine = TickingEngine()
    with _serving(create_app(_cfg(tmp_path), client=engine)) as base:
        status, body = _post(
            base + "/api/make",
            {"prompt": "x", "count": 1, "init_image": "../config/default.toml"},
        )
    assert status == 400
    assert "底图" in body["message"]
    assert engine.uploads == []


# ---------- 看图 ----------


def test_images_are_served_from_the_output_folder_only(tmp_path):
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    picture = _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with _serving(create_app(cfg, client=engine)) as base:
        status, data = _get(base + "/images/2026-10-23/fish-001.orig.png")
        assert status == 200
        assert data == picture.read_bytes()
        assert _get(base + "/images/../pyproject.toml")[0] == 404
        assert _get(base + "/images/2026-10-23/nope.png")[0] == 404


# ---------- 单张动作 ----------


def test_keep_puts_it_in_the_recipe_book(tmp_path):
    from lab.keeping import gallery

    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with _serving(create_app(cfg, client=engine)) as base:
        status, body = _post(
            base + "/api/keep", {"image": "2026-10-23/fish-001.orig.png"}
        )
    assert status == 200, body
    assert body["id"] == "fish-001"
    assert [entry.id for entry in gallery(cfg)] == ["fish-001"]


def test_sticker_makes_a_transparent_copy_right_here(tmp_path):
    from PIL import Image

    cfg = _cfg(tmp_path)
    picture = _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with StubEngine(mode="RGBA") as stub:
        from lab.comfy import EngineClient

        client = EngineClient(stub.host, stub.port, poll_interval=0.01, max_polls=50)
        with _serving(create_app(cfg, client=client)) as base:
            status, body = _post(
                base + "/api/sticker", {"image": "2026-10-23/fish-001.orig.png"}
            )
    assert status == 200, body
    assert body["id"] == "fish-001"
    assert body["file"] == "2026-10-23/fish-001.sticker.png"
    made = picture.parent / "fish-001.sticker.png"
    assert made.is_file()
    with Image.open(made) as opened:
        assert opened.mode == "RGBA"


def test_opening_the_folder_says_whether_it_worked(tmp_path):
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    opener = RecordingOpener()
    with _serving(create_app(cfg, client=engine, opener=opener)) as base:
        status, body = _post(
            base + "/api/open-folder", {"image": "2026-10-23/fish-001.orig.png"}
        )
    assert status == 200, body
    assert body["opened"] is True
    assert opener.folders == [cfg.paths.output_dir.resolve() / "2026-10-23"]


def test_a_folder_that_cannot_be_opened_is_reported_not_hidden(tmp_path):
    engine = TickingEngine()
    cfg = _cfg(tmp_path)
    _picture(tmp_path, "2026-10-23/fish-001.orig.png")
    with _serving(create_app(cfg, client=engine, opener=RecordingOpener(ok=False))) as base:
        status, body = _post(
            base + "/api/open-folder", {"image": "2026-10-23/fish-001.orig.png"}
        )
    assert status == 200
    assert body["opened"] is False
    assert body["message"], "没打开就得说清楚，不能默默什么都不发生"


# ---------- 拖进来的底图（判据 ③ 的"拖入"那条路） ----------


def _drop(base, name, blob):
    payload = {"name": name, "data": base64.b64encode(blob).decode("ascii")}
    return _post(base + "/api/base", payload)


def test_a_dropped_picture_lands_under_output(tmp_path):
    """拖进来的底图先收进 output，回一个相对路径 —— 页面拿它当底图用。"""
    cfg = _cfg(tmp_path)
    blob = png_bytes()
    with _serving(create_app(cfg, client=TickingEngine())) as base:
        status, body = _drop(base, "我的猫.png", blob)
    assert status == 200, body
    assert body["image"].endswith(".png")
    assert (Path(cfg.paths.output_dir) / body["image"]).read_bytes() == blob


def test_the_dropped_picture_can_serve_as_the_base_image(tmp_path):
    """收下来的那张立刻能当底图 —— 拖入这条路要闭环。"""
    cfg = _cfg(tmp_path)
    engine = TickingEngine()
    with _serving(create_app(cfg, client=engine)) as base:
        _, dropped = _drop(base, "cat.png", png_bytes())
        status, _ = _post(
            base + "/api/make",
            {
                "prompt": "chibi blue whale girl",
                "count": 1,
                "init_image": dropped["image"],
                "denoise": 0.45,
            },
        )
    assert status == 200
    assert engine.uploads, "底图得先交给引擎"
    assert engine.uploads[0].name == Path(dropped["image"]).name


def test_dropping_something_that_is_not_a_picture_is_refused(tmp_path):
    cfg = _cfg(tmp_path)
    with _serving(create_app(cfg, client=TickingEngine())) as base:
        status, body = _drop(base, "note.txt", b"hello")
    assert status == 400
    assert "底图" in body["message"]


def test_dropping_a_payload_that_cannot_be_read_is_refused(tmp_path):
    cfg = _cfg(tmp_path)
    with _serving(create_app(cfg, client=TickingEngine())) as base:
        status, body = _post(base + "/api/base", {"name": "cat.png", "data": "不是 base64"})
    assert status == 400
    assert body["message"]
