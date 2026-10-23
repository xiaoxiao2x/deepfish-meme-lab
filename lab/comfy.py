"""与 ComfyUI 对话。

只管四件事：提交任务、轮询结果、把图取回本地、把引擎的报错还原成人话。

**不启动引擎进程**（ADR 0004）——引擎不在就是不在，报清楚，让人自己去起。
进度走轮询（`GET /history/{id}`），WebSocket 留作将来的增强。

这一层是整个工作台唯一的对外接缝，所以它的每句话都得说准：
引擎活着的拒绝和引擎不在，是两种完全不同的故障，不能都报成"出错了"。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MAX_POLLS",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_TIMEOUT",
    "EngineClient",
    "EngineError",
    "EngineJobFailed",
    "EngineRejected",
    "EngineTimeout",
    "EngineUnreachable",
    "GeneratedImage",
    "JobResult",
]

DEFAULT_TIMEOUT = 30.0  # 单次 HTTP 的等待上限（秒）
DEFAULT_POLL_INTERVAL = 0.5
DEFAULT_MAX_POLLS = 1200  # 0.5s × 1200 ≈ 10 分钟


# ---------- 错误 ----------


class EngineError(Exception):
    """跟引擎打交道出的问题。消息是给人看的，不是给栈看的。"""


class EngineUnreachable(EngineError):
    """引擎不在那儿：端口没人听、连接被拒、或者压根没起。"""


class EngineRejected(EngineError):
    """引擎活着，但把这个任务退回来了（校验没过）。"""


class EngineJobFailed(EngineError):
    """任务提交成功了，但跑失败了。"""


class EngineTimeout(EngineError):
    """轮询次数用完了，任务还没完成。"""


# ---------- 数据 ----------


@dataclass(frozen=True)
class GeneratedImage:
    """引擎产出的一张图在引擎侧的位置。"""

    filename: str
    subfolder: str
    kind: str  # 引擎的 type 字段：output / temp
    node: str  # 产出它的节点 id


@dataclass(frozen=True)
class JobResult:
    prompt_id: str
    images: tuple[GeneratedImage, ...]
    files: tuple[Path, ...]
    history: dict  # 引擎的原始回复，留着当证据


# ---------- 客户端 ----------


class EngineClient:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8188,
        timeout: float = DEFAULT_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        max_polls: int = DEFAULT_MAX_POLLS,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_polls = max_polls

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ---------- 对外 ----------

    def version(self) -> str:
        """引擎自报的版本号。连不上会抛 EngineUnreachable。"""
        stats = _decode(self._request("/system_stats"))
        if isinstance(stats, dict):
            system = stats.get("system") or {}
            if system.get("comfyui_version"):
                return str(system["comfyui_version"])
        return "未知"

    def submit(self, prompt: dict, client_id: str | None = None) -> str:
        """提交一个 workflow（API 格式），拿回 prompt_id。"""
        payload: dict[str, Any] = {"prompt": prompt}
        if client_id:
            payload["client_id"] = client_id
        body = self._request("/prompt", payload=payload)
        data = _decode(body)
        if not isinstance(data, dict) or not data.get("prompt_id"):
            raise EngineError(f"引擎收下了任务却没给 prompt_id：{_snippet(body)}")
        return str(data["prompt_id"])

    def wait(
        self, prompt_id: str, on_poll: Callable[[int, float], None] | None = None
    ) -> JobResult:
        """轮询到任务完成。轮询次数用尽还没完就抛 EngineTimeout。

        `on_poll(polls, elapsed)` 每次查完没完成都会被叫一次 —— 上层拿它报进度。
        轮询能说的只有"还在等、等了多久"，绝不是"第几步"：步数只有 WebSocket
        的 progress 消息才有，轮询拿不到就别假装拿得到。
        """
        started = time.monotonic()
        for polls in range(1, self.max_polls + 1):
            entry = self._history(prompt_id)
            if entry is not None:
                return _result(prompt_id, entry)
            if on_poll is not None:
                on_poll(polls, time.monotonic() - started)
            time.sleep(self.poll_interval)
        waited = self.max_polls * self.poll_interval
        raise EngineTimeout(
            f"等了 {self.max_polls} 次（约 {waited:.0f} 秒），任务 {prompt_id} 还没完成。"
        )

    def fetch(self, image: GeneratedImage, dest_dir: Path) -> Path:
        """把引擎侧的一张图取回本地。"""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / Path(image.filename).name
        body = self._request(
            "/view",
            filename=image.filename,
            subfolder=image.subfolder,
            type=image.kind,
        )
        target.write_bytes(body)
        return target

    def generate(
        self,
        prompt: dict,
        dest_dir: Path,
        on_poll: Callable[[int, float], None] | None = None,
    ) -> JobResult:
        """提交 → 轮询 → 取回，一次走完。"""
        result = self.wait(self.submit(prompt), on_poll=on_poll)
        files = tuple(self.fetch(image, dest_dir) for image in result.images)
        return replace(result, files=files)

    # ---------- 内部 ----------

    def _request(self, path: str, payload: dict | None = None, **query: str) -> bytes:
        url = f"{self.base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise _http_error(exc.code, exc.read()) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise EngineUnreachable(self._unreachable_message()) from exc

    def _history(self, prompt_id: str) -> dict | None:
        """拿到条目就算完成；还没完成返回 None；跑失败直接抛。"""
        body = self._request(f"/history/{prompt_id}")
        data = _decode(body)
        if not isinstance(data, dict):
            raise EngineError(f"history 回的不是对象：{_snippet(body)}")
        entry = data.get(prompt_id)
        if not entry:
            return None
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            raise EngineJobFailed(_failure_message(entry))
        return entry

    def _unreachable_message(self) -> str:
        return (
            f"连不上 {self.host}:{self.port} —— 请先启动 ComfyUI"
            "（conda activate comfyui && python main.py）"
        )


# ---------- 消息组装 ----------


def _result(prompt_id: str, entry: dict) -> JobResult:
    images = []
    for node_id, output in (entry.get("outputs") or {}).items():
        for image in output.get("images") or []:
            if not image.get("filename"):
                continue
            images.append(
                GeneratedImage(
                    filename=image["filename"],
                    subfolder=image.get("subfolder", ""),
                    kind=image.get("type", "output"),
                    node=str(node_id),
                )
            )
    if not images:
        raise EngineJobFailed(f"任务 {prompt_id} 报完成了，却没产出任何图片。")
    return JobResult(prompt_id=prompt_id, images=tuple(images), files=(), history=entry)


def _http_error(code: int, body: bytes) -> EngineError:
    detail = _decode(body)
    if (
        code == 400
        and isinstance(detail, dict)
        and ("node_errors" in detail or "error" in detail)
    ):
        return EngineRejected(_rejection_message(detail))
    return EngineError(f"引擎返回 {code}：{_snippet(body)}")


def _rejection_message(detail: dict) -> str:
    error = detail.get("error") or {}
    lines = [f"引擎拒绝了这个任务：{error.get('message') or '（没说原因）'}"]
    for node_id, node in (detail.get("node_errors") or {}).items():
        class_type = node.get("class_type") or "?"
        for item in node.get("errors") or []:
            lines.append(
                f"  节点 {node_id}（{class_type}）："
                f"{item.get('message', '')} {item.get('details', '')}".rstrip()
            )
    return "\n".join(lines)


def _failure_message(entry: dict) -> str:
    lines = ["引擎说这个任务跑失败了。"]
    for message in (entry.get("status") or {}).get("messages") or []:
        if not isinstance(message, (list, tuple)) or len(message) != 2:
            continue
        kind, payload = message
        if not isinstance(payload, dict):
            continue
        node = payload.get("node_type") or payload.get("node_id") or "?"
        reason = payload.get("exception_message") or payload.get("exception_type") or ""
        lines.append(
            f"  {kind}：节点 {node} —— {reason}" if reason else f"  {kind}：节点 {node}"
        )
    return "\n".join(lines)


def _decode(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _snippet(body: bytes, limit: int = 200) -> str:
    text = body[:limit].decode("utf-8", "replace")
    return text + ("…" if len(body) > limit else "")
