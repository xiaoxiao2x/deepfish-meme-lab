"""后台出片任务（M5.2）：事件说了什么、失败也要说话、落盘与记账。

接缝在 `JobBoard.start` 的 `client` 参数上：换上自带的假引擎，就能不看时钟、
不碰真引擎地验"事件说了什么"。真引擎那条路在 test_web_api.py 里对着 StubEngine 验。

假引擎这一份与 tests/test_pipeline.py 的 RecordingEngine 是同类替身，各文件自带 ——
tests/ 没有 conftest，跨文件 import 兄弟测试模块比重复这三十行更脆。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import replace
from datetime import date
from io import BytesIO
from pathlib import Path

from PIL import Image

from lab.comfy import EngineError, GeneratedImage, JobResult
from lab.config import Config, load_config
from lab.jobs import Job, JobBoard
from lab.pipeline import spec_for
from lab.registry import load_entries, record_path

REPO_ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 23)


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class TickingEngine:
    """够 EngineLike 那四件事的替身：图是一张固定的真 PNG。

    `polls` 是每次 wait 里报的（轮询次数, 已等秒数）—— 进度节流就是照它验的。
    """

    def __init__(
        self,
        *,
        blow_up_on: int | None = None,
        polls: tuple[tuple[int, float], ...] = ((1, 0.2),),
    ) -> None:
        self.prompts: list[dict] = []
        self._blow_up_on = blow_up_on
        self._polls = polls
        self._counter = 0

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        self._counter += 1
        if self._blow_up_on is not None and self._counter >= self._blow_up_on:
            raise EngineError("引擎说这份 prompt 它不收")
        self.prompts.append(prompt)
        return f"job-{self._counter}"

    def wait(self, prompt_id: str, on_poll=None) -> JobResult:
        if on_poll is not None:
            for polls, elapsed in self._polls:
                on_poll(polls, elapsed)
        image = GeneratedImage(
            filename="fish/txt2img_00001_.png", subfolder="", kind="output", node="13"
        )
        return JobResult(prompt_id=prompt_id, images=(image,), files=(), history={})

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(image.filename).name
        target.write_bytes(png_bytes())
        return target

    def upload_image(self, path: Path, name: str | None = None) -> str:
        return name or Path(path).name


def _cfg(tmp_path: Path) -> Config:
    """一个临时仓库：模板带过去，图与账都落在 tmp 里，不碰真仓库的 output/。"""
    shutil.copytree(REPO_ROOT / "workflows", tmp_path / "workflows", dirs_exist_ok=True)
    cfg = load_config(REPO_ROOT)
    return replace(
        cfg,
        repo_root=tmp_path,
        paths=replace(cfg.paths, output_dir=tmp_path / "output"),
    )


def _drain(job: Job, limit: float = 10.0) -> list[dict]:
    """读到结束事件为止。读到一半就停，失败会变成一句看不懂的"超时"。"""
    events: list[dict] = []
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        event = job.read(timeout=0.2)
        if event is None:
            continue
        events.append(event)
        if event["type"] in {"finished", "failed"}:
            return events
    raise AssertionError(f"任务没在 {limit} 秒内结束，只收到：{events}")


def _run(tmp_path: Path, count: int = 2, **kwargs) -> tuple[JobBoard, Job, list[dict]]:
    # 默认带一件假引擎：不给 client 的话 make 会去连本机那个真 ComfyUI ——
    # 单测不该排队占显卡，也不该让"跑得快"取决于引擎在不在。
    kwargs.setdefault("client", TickingEngine())
    board = JobBoard()
    config = _cfg(tmp_path)
    spec = spec_for(config, prompt="chibi blue whale girl", seed=12345)
    job = board.start(config, spec, count, today=TODAY, **kwargs)
    return board, job, _drain(job)


def test_a_job_says_start_then_done_then_finished(tmp_path):
    _, _, events = _run(tmp_path, count=2)

    assert [event["type"] for event in events] == [
        "start",
        "done",
        "start",
        "done",
        "finished",
    ]
    finished = events[-1]
    assert finished["made"] == 2
    assert finished["folder"] == "2026-10-23"


def test_every_done_event_points_at_a_file_and_a_number(tmp_path):
    _, _, events = _run(tmp_path, count=2)

    done = [event for event in events if event["type"] == "done"]
    assert [event["id"] for event in done] == ["fish-001", "fish-002"]
    assert [event["seed"] for event in done] == [12345, 12346]
    for event in done:
        assert event["file"] == f"2026-10-23/{event['id']}.orig.png"
        assert (tmp_path / "output" / event["file"]).is_file()


def test_every_image_of_the_batch_lands_in_the_day_ledger(tmp_path):
    _, _, events = _run(tmp_path, count=2)

    ledger = record_path(tmp_path / "output", TODAY)
    assert len(load_entries(ledger)) == 2
    assert len([e for e in events if e["type"] == "done"]) == 2


def test_a_job_that_blows_up_tells_the_page_why(tmp_path):
    _, job, events = _run(tmp_path, count=3, client=TickingEngine(blow_up_on=2))

    assert events[-1]["type"] == "failed"
    assert "引擎没干成：" in events[-1]["message"]
    # 第一张已经出成了 —— 页面上那几张不该跟着白等
    assert events[-1]["made"] == 1
    assert job.state == "failed"
    assert job.error == events[-1]["message"]
    assert (tmp_path / "output" / "2026-10-23" / "fish-001.orig.png").is_file()


def test_progress_is_paced_like_the_command_line(tmp_path):
    engine = TickingEngine(polls=((1, 1.0), (5, 5.0), (9, 5.5)))
    _, _, events = _run(tmp_path, count=1, client=engine)

    waiting = [event for event in events if event["type"] == "waiting"]
    # 1 秒太早、5.5 秒离上一条才 0.5 秒 —— 只有 5.0 那条该说出来
    assert waiting == [{"type": "waiting", "index": 1, "total": 1, "elapsed": 5.0}]


def test_events_survive_json(tmp_path):
    _, _, events = _run(tmp_path, count=1)

    for event in events:
        assert json.loads(json.dumps(event)) == event


def test_an_unknown_job_is_simply_not_there():
    board = JobBoard()

    assert board.get("nope") is None
    assert board.get("") is None
