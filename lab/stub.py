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
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (64, 64), (30, 120, 220)).save(buffer, format="PNG")
    return buffer.getvalue()


def parse_multipart(content_type: str, body: bytes) -> dict[str, tuple[str, bytes]]:
    """够用的 multipart/form-data 解析：字段名 →（文件名，内容）。

    只认我们自己发的那个形状（见 lab.comfy 里的 _multipart）——stub 的职责是
    "真把这张图收下"，不是当通用 MIME 解析器。
    """
    marker = "boundary="
    if marker not in content_type:
        raise ValueError("不是 multipart：Content-Type 里没有 boundary")
    boundary = content_type.split(marker, 1)[1].strip().strip('"')
    separator = b"--" + boundary.encode("utf-8")
    parts: dict[str, tuple[str, bytes]] = {}
    for chunk in body.split(separator):
        chunk = chunk.lstrip(b"\r\n")
        if not chunk or chunk.startswith(b"--"):
            continue
        head, _, payload = chunk.partition(b"\r\n\r\n")
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        disposition = ""
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-disposition:"):
                disposition = line.decode("utf-8", "replace")
        name = _disposition_value(disposition, "name")
        if not name:
            continue
        parts[name] = (_disposition_value(disposition, "filename") or "", payload)
    return parts


def _disposition_value(header: str, key: str) -> str:
    """从 Content-Disposition 里抠 name="..." / filename="..." 的值。"""
    prefix = key + '="'
    start = header.find(prefix)
    if start < 0:
        return ""
    rest = header[start + len(prefix) :]
    end = rest.find('"')
    return rest[:end] if end >= 0 else rest


class _Handler(BaseHTTPRequestHandler):
    engine: StubEngine

    def log_message(self, *args) -> None:  # 别往 stderr 刷日志
        pass

    # ---------- 写 ----------

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/prompt":
            self._prompt()
            return
        if path == "/upload/image":
            self._upload()
            return
        self._json(404, {"error": {"message": "stub 没有这个接口"}})

    def _prompt(self) -> None:
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
        number = self.engine.register(prompt_id, body.get("prompt") or {})
        self._json(200, {"prompt_id": prompt_id, "number": number, "node_errors": {}})

    def _upload(self) -> None:
        """收一张底图，形状按 ComfyUI 的 POST /upload/image 来。"""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            parts = parse_multipart(self.headers.get("Content-Type") or "", body)
        except ValueError as exc:
            self._json(400, {"error": {"message": str(exc)}})
            return
        image = parts.get("image")
        if image is None or not image[0] or not image[1]:
            self._json(400, {"error": {"message": "multipart 里没有 image 文件"}})
            return
        filename, payload = image
        name = self.engine.store_upload(filename, payload)
        self._json(200, {"name": name, "subfolder": "", "type": "input"})

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
        #: 收到过的底图：引擎侧文件名 → 原始字节（真引擎会落到 input 目录）。
        self.uploads: dict[str, bytes] = {}
        #: 收到过的 prompt 图，按提交顺序 —— 验"模板里写的是不是上传的那个名字"。
        self.prompts: list[dict] = []

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

    def register(self, prompt_id: str, prompt: dict | None = None) -> int:
        self.submitted += 1
        self._polls[prompt_id] = 0
        if prompt is not None:
            self.prompts.append(prompt)
        return self.submitted

    def store_upload(self, filename: str, payload: bytes) -> str:
        """收下底图，返回引擎侧的名字（真引擎会给重名的加 (1)，stub 不折腾）。"""
        self.uploads[filename] = payload
        return filename

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
