"""后台出片任务（M5.2）：一条线程跑一批，事件排着队等页面来读。

页面不能像命令行那样"等它跑完"：一张图几十秒，HTTP 请求挂在那儿就是白屏。
所以这里把 pipeline.make 挪进线程，把它的三次进度回调翻成事件；路由那头
用 SSE 读走这些事件，浏览器端不轮询。

**这一层不做判断**：出片规则在 pipeline、记账在 registry、去背在 postprocess。
它只回答三个问题 —— 跑到哪了、成没成、成了的图在哪。
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .comfy import EngineError, EngineLike
from .config import Config
from .naming import entry_id, parse_serial
from .paths import output_relative
from .pipeline import PacedProgress, make
from .workflows import GenSpec, WorkflowError

__all__ = ["Job", "JobBoard"]


@dataclass
class Job:
    """一次后台出片。事件躺在队列里，读走就不再来。"""

    id: str
    spec: GenSpec
    count: int
    events: queue.Queue = field(default_factory=queue.Queue, repr=False)

    #: running / done / failed —— 页面拿它决定还转不转圈
    state: str = "running"
    #: 已经出成几张（失败时也报出来，不然页面上那几张跟着白等）
    made: int = 0
    error: str = ""

    def read(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """取一条事件；timeout 秒内没有就 None（不是失败，是还没轮到）。"""
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def put(self, event: dict[str, Any]) -> None:
        self.events.put(event)


class JobBoard:
    """挂着还没被忘掉的任务。

    不落盘：这些任务活不过进程重启 —— 重启之后引擎队列里那些才是真相。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(
        self,
        config: Config,
        spec: GenSpec,
        count: int,
        *,
        client: EngineLike | None = None,
        today: date | None = None,
    ) -> Job:
        """开一条线程跑一批，立刻把任务还回去 —— 进度从 job.read 里读。"""
        job = Job(id=uuid.uuid4().hex[:12], spec=spec, count=count)
        with self._lock:
            self._jobs[job.id] = job
        thread = threading.Thread(
            target=self._run,
            args=(config, job, client, today),
            name=f"job-{job.id}",
            daemon=True,
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(
        self,
        config: Config,
        job: Job,
        client: EngineLike | None,
        today: date | None,
    ) -> None:
        progress = _JobProgress(job, config)
        try:
            made = make(
                config,
                job.spec,
                count=job.count,
                progress=progress,
                client=client,
                today=today,
            )
        except Exception as exc:
            # 这里**必须**收住所有异常：线程里漏一个出去，页面就永远转圈，
            # 而人看到的是一个没有报错、也没有图的页面。翻成人话由 _reason 负责。
            job.state = "failed"
            job.error = _reason(exc)
            job.put({"type": "failed", "message": job.error, "made": job.made})
            return
        job.state = "done"
        job.made = len(made)
        job.put({"type": "finished", "made": len(made), "folder": progress.folder})


class _JobProgress(PacedProgress):
    """把 pipeline 的三次回调翻成三种事件。

    什么时候说由 PacedProgress 定（与命令行同一套节奏），这里只管怎么说：
    往任务的队列里放一条事件。
    """

    def __init__(self, job: Job, config: Config) -> None:
        super().__init__()
        self._job = job
        self._config = config
        self.folder = ""

    def say_start(self, index: int, total: int, seed: int) -> None:
        self._job.put({"type": "start", "index": index, "total": total, "seed": seed})

    def say_waiting(self, index: int, total: int, elapsed: float) -> None:
        self._job.put(
            {
                "type": "waiting",
                "index": index,
                "total": total,
                "elapsed": round(elapsed, 1),
            }
        )

    def say_done(self, index: int, total: int, path: Path, seed: int) -> None:
        self._job.made = index
        self.folder = self._folder_of(path)
        self._job.put(
            {
                "type": "done",
                "index": index,
                "total": total,
                "seed": seed,
                "id": _id_of(path),
                "file": self._relative(path),
            }
        )

    def _relative(self, path: Path) -> str:
        """图在 output 下的相对路径 —— 页面拿它拼 /images/<这里>。"""
        return output_relative(self._config.paths.output_dir, path)

    def _folder_of(self, path: Path) -> str:
        """落在 output 下的哪个日期目录（相对路径），给"打开文件夹"用。"""
        relative = self._relative(path)
        parent = Path(relative).parent
        return "" if parent == Path() else parent.as_posix()


def _id_of(path: Path) -> str:
    """这张图叫什么号 —— 与 pipeline 落账时用的是同一处号（naming）。"""
    serial = parse_serial(path)
    return entry_id(serial) if serial is not None else Path(path).stem


def _reason(exc: Exception) -> str:
    """异常翻成页面上那一行话，措辞与命令行一致。

    引擎家族、模板错误、参数错误本身就写着人话（都是"哪里不对 —— 怎么办"），
    原样端出去；真出了别的毛病就把类型名带上，不然页面上只剩一句看不懂的英文。
    """
    message = str(exc).strip()
    if isinstance(exc, EngineError):
        return f"引擎没干成：{message}"
    if isinstance(exc, WorkflowError):
        return f"工作流模板有问题：{message}"
    if isinstance(exc, ValueError):
        return message
    return f"{type(exc).__name__}：{message}" if message else type(exc).__name__
