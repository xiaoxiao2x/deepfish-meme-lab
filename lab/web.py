"""本地界面：把工作台已经能做的事端到浏览器。

四个页面（出片台 / 素材库 / 成品库 / 设置），每页一个路由、一份 Jinja2 页面；
样式跟着包走，没有构建步骤 —— 改完刷新浏览器就看见（design.md §9.1）。

这一层**不做判断**：动作都在已经能跑的模块里，这里只把它们摆到页面上。
出片交给 `lab.jobs` 的线程 + `lab.pipeline`，去背与保留直接调 `lab.postprocess`
与 `lab.keeping` —— 命令行里能做的，页面里做的是同一件事、落的是同一本账
（参数翻译也只有 `pipeline.spec_for` 一份来源，那正是判据 ② 要守的）。

进度不靠浏览器轮询：`/api/jobs/<号>/events` 是一条 SSE 长连接，后端把线程里的
事件原样推出来；引擎状态同理，每隔几秒推一条（design.md §9.1）。

地址取自 [server]（本机实际值可以写在 config/local.toml），命令行给的覆盖它。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .comfy import EngineClient, EngineError, EngineLike
from .config import Config
from .jobs import Job, JobBoard
from .naming import daily_dir
from .paths import output_relative
from .pipeline import spec_for

__all__ = ["PAGES", "STATIC_DIR", "TEMPLATES_DIR", "Page", "create_app", "serve"]

SITE_NAME = "鱼片加工厂"

#: 页面本身（HTML 与 CSS）跟代码放一起：它们是这个包的一部分，不是仓库外的另一摊资源。
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

#: 浏览器那侧取这些文件的路由前缀。
STATIC_MOUNT = "/static"

#: 看图的前缀：页面手里的是相对 output 的路径，拼在这儿就是一张图。
IMAGES_MOUNT = "/images"

#: 页面调的那些接口都挂在这儿。
API_PREFIX = "/api"

#: 引擎状态多久推一次 —— 节奏由服务端定，浏览器端不轮询。
ENGINE_PUSH_EVERY = 5.0

#: 任务队列里没消息时，多久发一条注释行；长连接闲着会被中间的代理掐掉。
SSE_KEEPALIVE = 1.0

#: SSE 该带的头：不缓存、别攒着不发。
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

#: 拖进来的底图只认这几种后缀 —— 引擎那边也只吃这几样。
BASE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

#: 拖进来的底图大小上限：16 MB。4K 的 PNG 也装得下，顺手挡住把内存吃光的那种。
BASE_MAX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class Page:
    """一个页面：路由、导航上叫什么、用哪份 Jinja2 页面。"""

    path: str
    key: str
    title: str
    template: str


#: 四个页面，顺序就是导航上的顺序（design.md §9.2）。头一个是出片台 ——
#: 它是主页面，九成时间待在那儿。
PAGES: tuple[Page, ...] = (
    Page(path="/", key="generate", title="出片台", template="generate.html"),
    Page(path="/assets", key="assets", title="素材库", template="assets.html"),
    Page(path="/gallery", key="gallery", title="成品库", template="gallery.html"),
    Page(path="/settings", key="settings", title="设置", template="settings.html"),
)


class WebEngine(EngineLike, Protocol):
    """页面要用的引擎：能出片（EngineLike 那四个），还能报个版本、数一下队列长度。

    状态条那三样比出片本身多要两个方法，所以在这里补成一个更宽的协议 ——
    引擎客户端的传参类型没被放宽，页面的替身也不必真去连引擎。
    """

    def version(self) -> str:
        """引擎版本；取不到的自有说法。"""
        ...

    def queue(self) -> tuple[int, int]:
        """在算几件、排着几件。"""
        ...


class MakeRequest(BaseModel):
    """出片台那张表单。字段与命令行同名，没填的都按 config/default.toml 走。"""

    prompt: str = ""
    #: 张数留空就按 config 的 [generate].count 走 —— 那个数只留一处来源。
    count: int | None = None
    negative: str | None = None
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None
    width: int | None = None
    height: int | None = None
    init_image: str | None = None
    denoise: float | None = None


class ImageRequest(BaseModel):
    """对某一张做点什么的那些动作：去背、保留、打开它所在的文件夹。"""

    image: str = ""


class BaseRequest(BaseModel):
    """拖进来当底图的那张图：原文件名 + base64。

    不走 multipart 是刻意的：那要再引一个包，而这里的图来自本机浏览器、
    一次一张，base64 够用，也就少一样依赖。
    """

    name: str = ""
    data: str = ""


def _output_image(config: Config, raw: str) -> Path:
    """把页面送来的相对路径还原成 output 里的真路径。

    越界（`..`、绝对路径、符号链接绕出去）与不存在都当同一件事报：这张图不在
    output 里。校验落在 realpath 之后 —— 绕不过去。
    """
    root = Path(config.paths.output_dir).resolve()
    candidate = (root / str(raw)).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise ValueError(f"这张不在 output 里：{raw}")
    return candidate


def _check_make(config: Config, payload: MakeRequest) -> int:
    """先看懂页面送来的表单：说不通就当场抛，通过就回这次要出几张。

    拒绝话都走 ValueError —— 下面那个处理器统一翻成 400，
    各条路由不必各拼一次响应（没填的张数也在这里落成 config 里那个数）。
    """
    if not payload.prompt.strip():
        raise ValueError("总得告诉它画点什么 —— 台词那一栏是空的。")
    count = payload.count if payload.count is not None else config.generate.count
    if not 1 <= count <= 8:
        raise ValueError(f"一次出 1 到 8 张：给了 {count}。")
    if payload.denoise is not None and not payload.init_image:
        raise ValueError("重绘强度只在选了底图时才有用 —— 先挑一张底图。")
    return count


def _job_spec_event(config: Config, job: Job) -> dict[str, Any]:
    """这批是用什么参数下的单。

    卡片上的「重跑这张」要照这一张自己的参数再出一张 —— 不是照此刻表单里
    写着什么，也不能拿它显示的那张图当底图（去背之后那张是贴纸）。
    """
    base = ""
    if job.spec.init_image is not None:
        base = output_relative(config.paths.output_dir, job.spec.init_image)
    return {
        "type": "spec",
        "prompt": job.spec.prompt,
        "base": base,
        "denoise": job.spec.denoise if base else None,
    }


def _engine_state(config: Config, client: WebEngine | None) -> dict[str, Any]:
    """引擎在不在、什么版本、排了多少 —— 状态条要的那几样。

    连不上不是这个服务的错：如实报 ok=False 带上原因，页面照实显示。
    """
    engine = client or EngineClient(config.comfy.host, config.comfy.port)
    try:
        version = engine.version()
        running, pending = engine.queue()
    except EngineError as exc:
        return {
            "ok": False,
            "version": "",
            "running": 0,
            "pending": 0,
            "message": str(exc),
        }
    return {
        "ok": True,
        "version": version,
        "running": running,
        "pending": pending,
        "message": "",
    }


def _base_name(name: str, digest: str) -> str:
    """给拖进来的这张图造个落点：留原名、去掉怪字符，尾巴上挂一小段指纹防撞。"""
    stem = Path(name).stem or "base"
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", stem).strip("-_") or "base"
    return f"{safe[:40]}-{digest}.png"


def _save_base(config: Config, payload: BaseRequest) -> str:
    """把拖进来的底图收进今天那个目录，回一个相对 output 的路径。

    这一层不记账：它只是一张"拿来当底图"的图，入库是素材库（M5.4）的事。
    """
    suffix = Path(payload.name).suffix.lower()
    if suffix not in BASE_SUFFIXES:
        raise ValueError(
            f"底图得是这几样之一：{'、'.join(BASE_SUFFIXES)} —— "
            f"这个看着像是 {suffix or '没有后缀'}。"
        )
    try:
        blob = base64.b64decode(payload.data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("这张底图没能读出来 —— 换一张，或者先存成 PNG 再拖进来。") from exc
    if not blob:
        raise ValueError("这张底图是空的 —— 换一张。")
    if len(blob) > BASE_MAX_BYTES:
        megabytes = BASE_MAX_BYTES // (1024 * 1024)
        raise ValueError(
            f"底图太大了（{len(blob) // (1024 * 1024)} MB）—— 压到 {megabytes} MB 以内再来。"
        )
    folder = daily_dir(Path(config.paths.output_dir))
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.blake2s(blob, digest_size=4).hexdigest()
    target = folder / _base_name(payload.name, digest)
    target.write_bytes(blob)
    return output_relative(config.paths.output_dir, target)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _open_folder(folder: Path) -> bool:
    """弹出这个文件夹。没有桌面的机器上不假装成功 —— 返回 False，页面照实说。"""
    try:
        done = subprocess.run(
            ["xdg-open", str(folder)], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def _page_handler(
    page: Page, templates: Jinja2Templates, config: Config
) -> Callable[[Request], HTMLResponse]:
    """给一个页面造它的处理函数。

    PAGES 是唯一来源：加一页只改上面那一处，这里跟着长出来。
    """

    def handler(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            page.template,
            {
                "site_name": SITE_NAME,
                "page": page,
                "pages": PAGES,
                "repo_root": str(config.repo_root),
                "engine_endpoint": config.comfy.endpoint,
                "images_mount": IMAGES_MOUNT,
                "api": API_PREFIX,
                "default_count": config.generate.count,
            },
        )

    return handler


def create_app(
    config: Config,
    *,
    client: WebEngine | None = None,
    opener: Callable[[Path], bool] | None = None,
) -> FastAPI:
    """把这个仓库装进一个 app。`lab serve` 与测试都从这儿进来。

    `client` 与 `opener` 是两个可注入的接缝：测试拿假引擎顶掉真引擎、拿一个记录用的
    函数顶掉"真去弹文件夹"。生产路径都不传，走真东西。
    """
    app = FastAPI(title=SITE_NAME, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    board = JobBoard()
    open_folder = opener or _open_folder

    @app.exception_handler(ValueError)
    async def _refused(_request: Request, exc: ValueError) -> JSONResponse:
        """页面送来的东西说不通 —— 一律 400，原话就是给人看的那一句。"""
        return JSONResponse({"message": str(exc)}, status_code=400)

    @app.exception_handler(EngineError)
    async def _engine_down(_request: Request, exc: EngineError) -> JSONResponse:
        """引擎不在、或者不肯干 —— 503：这不是页面说错了话，是后面那台机器的事。"""
        return JSONResponse({"message": f"引擎没干成：{exc}"}, status_code=503)

    app.mount(STATIC_MOUNT, StaticFiles(directory=str(STATIC_DIR)), name="static")
    for page in PAGES:
        app.get(page.path, response_class=HTMLResponse, name=page.key)(
            _page_handler(page, templates, config)
        )

    @app.get(f"{IMAGES_MOUNT}/{{relative:path}}", name="images")
    def image(relative: str) -> Any:
        try:
            path = _output_image(config, relative)
        except ValueError:
            return Response(status_code=404)
        return FileResponse(path)

    @app.get(f"{API_PREFIX}/engine", name="engine-state")
    def engine_state() -> dict[str, Any]:
        return _engine_state(config, client)

    @app.get(f"{API_PREFIX}/engine/events", name="engine-events")
    def engine_events() -> StreamingResponse:
        def stream() -> Iterator[str]:
            try:
                while True:
                    yield _sse({"type": "engine", **_engine_state(config, client)})
                    time.sleep(ENGINE_PUSH_EVERY)
            except GeneratorExit:  # 浏览器关掉页面了，别留着这条线
                return

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    @app.post(f"{API_PREFIX}/base", name="base")
    def base(payload: BaseRequest) -> dict[str, Any]:
        """拖进来的底图：收下、落进今天的目录、回一个相对路径。"""
        return {"image": _save_base(config, payload)}

    @app.post(f"{API_PREFIX}/make", name="make")
    def make_endpoint(payload: MakeRequest) -> dict[str, Any]:
        count = _check_make(config, payload)
        base: Path | None = None
        if payload.init_image:
            try:
                base = _output_image(config, payload.init_image)
            except ValueError as exc:
                raise ValueError(
                    f"底图不在 output 里：{payload.init_image}"
                    " —— 从素材库挑一张，或先出一张当底。"
                ) from exc
        spec = spec_for(
            config,
            prompt=payload.prompt,
            negative=payload.negative,
            seed=payload.seed,
            steps=payload.steps,
            cfg=payload.cfg,
            width=payload.width,
            height=payload.height,
            init_image=base,
            denoise=payload.denoise,
        )
        job = board.start(config, spec, count, client=client)
        return {"job": job.id}

    @app.get(f"{API_PREFIX}/jobs/{{job_id}}/events", name="job-events")
    def job_events(job_id: str) -> Any:
        job = board.get(job_id)
        if job is None:
            return JSONResponse({"message": f"没有这个任务：{job_id}"}, status_code=404)

        def stream() -> Iterator[str]:
            # 先报一次这批的参数：卡片手里有了它，才能照「自己那张」重跑
            yield _sse(_job_spec_event(config, job))
            while True:
                event = job.read(timeout=SSE_KEEPALIVE)
                if event is None:
                    yield ": 还在跑\n\n"
                    continue
                yield _sse(event)
                if event.get("type") in ("finished", "failed"):
                    return

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    @app.post(f"{API_PREFIX}/sticker", name="sticker")
    def sticker_endpoint(payload: ImageRequest) -> dict[str, Any]:
        from .postprocess import sticker  # 去背要 Pillow，到用的时候再 import

        made = sticker(config, _output_image(config, payload.image), client=client)
        return {
            "id": made.id,
            "file": output_relative(config.paths.output_dir, made.path),
            "model": made.model,
        }

    @app.post(f"{API_PREFIX}/keep", name="keep")
    def keep_endpoint(payload: ImageRequest) -> dict[str, Any]:
        from .keeping import keep  # 量成品的宽高要 Pillow，到用的时候再 import

        entry, _from_today = keep(config, _output_image(config, payload.image))
        return {"id": entry.id, "kind": entry.kind}

    @app.post(f"{API_PREFIX}/open-folder", name="open-folder")
    def open_folder_endpoint(payload: ImageRequest) -> dict[str, Any]:
        folder = _output_image(config, payload.image).parent
        opened = bool(open_folder(folder))
        message = "" if opened else f"没能自己弹出来 —— 手动打开这个文件夹：{folder}"
        return {"opened": opened, "folder": str(folder), "message": message}

    return app


def serve(config: Config, *, host: str | None = None, port: int | None = None) -> int:
    """起服务，一直服务到 Ctrl-C。返回进程退出码。"""
    import uvicorn

    server = config.server.overridden(host=host, port=port)

    # flush：这条得立刻出现。把输出接进管道或日志时，不 flush 就会压在缓冲区里，
    # 用户盯着一个空终端等半天 —— 而判据就是「起得来，终端上有可点的地址」。
    print(f"工作台起在 {server.address} —— 浏览器打开它，Ctrl-C 收工。", flush=True)
    uvicorn.run(create_app(config), host=server.host, port=server.port, log_level="warning")
    return 0
