"""冒充 ComfyUI 的本地 stub 引擎。

它认提交、答查询、返回一张预置的小图。用途是让工作台在**没有显卡、没有真引擎**
的机器上也能端到端跑通 —— 测试用它，`python -m lab stub` 手动调试也用它。

它不假装懂工作流：收到什么 prompt 都收下，按固定节奏"生成完"。所以拿它验的是
「工作台有没有把话说对」，不是「模型画得对不对」——后者只有真引擎能回答。
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import TracebackType
from typing import Self
from urllib.parse import parse_qs, urlparse

from PIL import Image

__all__ = ["STUB_FILENAME", "StubEngine", "default_image"]

STUB_FILENAME = "fish-stub_00001_.png"

#: 引擎侧那套错误长这样（真的 ComfyUI 校验失败时也是这个形状），
#: 客户端必须能把它还原成人话。
REJECTION = {
    "error": {
        "type": "prompt_outputs_failed_validation",
        "message": "Prompt outputs failed validation",
        "details": "",
    },
    "node_errors": {
        "3": {
            "errors": [
                {
                    "type": "value_not_in_list",
                    "message": "Value not in list",
                    "details": "flux-2-klein-9b-fp8.safetensors not in []",
                }
            ],
            "class_type": "UNETLoader",
        }
    },
}


def default_image() -> bytes:
    """一张真 PNG，够用来验「取回文件」这一段。"""
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (30, 120, 220)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Handler(BaseHTTPRequestHandler):
    engine: StubEngine

    def log_message(self, *args) -> None:  # 别往 stderr 刷日志
        pass

    # ---------- 写 ----------

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/prompt":
            self._json(404, {"error": {"message": "stub 没有这个接口"}})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            self._json(400, {"error": {"message": f"body 不是 JSON：{exc}"}})
            return
        if not body.get("prompt"):
            self._json(
                400,
                {
                    "error": {"type": "invalid_prompt", "message": "body 里没有 prompt"},
                    "node_errors": {},
                },
            )
            return
        if self.engine.reject_prompts:
            self._json(400, REJECTION)
            return

        prompt_id = uuid.uuid4().hex
        number = self.engine.register(prompt_id)
        self._json(200, {"prompt_id": prompt_id, "number": number, "node_errors": {}})

    # ---------- 读 ----------

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/system_stats":
            self._json(
                200,
                {
                    "system": {
                        "comfyui_version": "0.0.0-stub",
                        "python_version": "stub",
                        "argv": ["stub"],
                    }
                },
            )
            return

        if parsed.path.startswith("/history/"):
            prompt_id = parsed.path.rsplit("/", 1)[-1]
            entry = self.engine.history_entry(prompt_id)
            self._json(200, {prompt_id: entry} if entry else {})
            return

        if parsed.path == "/view":
            query = parse_qs(parsed.query)
            if not query.get("filename"):
                self._json(400, {"error": {"message": "缺 filename"}})
                return
            self._bytes(200, "image/png", self.engine.image)
            return

        self._json(404, {"error": {"message": "stub 没有这个接口"}})

    # ---------- 输出 ----------

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._bytes(code, "application/json", body)

    def _bytes(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubEngine:
    """一个跑在后台线程里的假引擎。

        with StubEngine() as engine:
            client = EngineClient(engine.host, engine.port)

    `ticks_before_done` 让 `/history` 前几次故意回空 —— 逼客户端真的去轮询，
    而不是"查一次运气好就拿到"。
    """

    def __init__(
        self,
        ticks_before_done: int = 1,
        reject_prompts: bool = False,
        filename: str = STUB_FILENAME,
        image: bytes | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.ticks_before_done = ticks_before_done
        self.reject_prompts = reject_prompts
        self.filename = filename
        self.image = image if image is not None else default_image()

        self.host = host
        self.port = port  # 0 = 让系统挑一个
        self.submitted = 0  # 一共收下过几次提交

        self._polls: dict[str, int] = {}
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------

    def start(self) -> Self:
        handler = type("_BoundHandler", (_Handler,), {"engine": self})
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # ---------- 给 handler 用 ----------

    def register(self, prompt_id: str) -> int:
        self.submitted += 1
        self._polls[prompt_id] = 0
        return self.submitted

    def history_entry(self, prompt_id: str) -> dict | None:
        if prompt_id not in self._polls:
            return None
        self._polls[prompt_id] += 1
        if self._polls[prompt_id] <= self.ticks_before_done:
            return None  # 还在"生成"
        return {
            "prompt": [],
            "outputs": {
                "9": {
                    "images": [
                        {"filename": self.filename, "subfolder": "", "type": "output"}
                    ]
                }
            },
            "status": {"status_str": "success", "completed": True, "messages": []},
        }
