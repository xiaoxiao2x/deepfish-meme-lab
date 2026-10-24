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

from . import showcase
from .comfy import EngineClient, EngineError, EngineLike
from .config import Config
from .jobs import Job, JobBoard
from .naming import daily_dir
from .paths import output_relative
from .pipeline import spec_for
from .recording import resolve_recorded
from .registry import Entry

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

#: 成品库的缩略图：账里那一条按（日期/号/阶段.png）现取现缩。
THUMB_MOUNT = "/thumb"

#: 当天的全量记录里那一条，原样摊成 JSON。
RECORD_MOUNT = "/record"

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


#: 出片台那条路。「用同参数重跑」从这里跳回去 —— 导航那个循环里已经有它了，
#: 成品库只是要一个能拼查询串的地址，不必再从 PAGES 里现找一遍。
GENERATE_PATH = next(page for page in PAGES if page.key == "generate").path


@dataclass(frozen=True)
class CardView:
    """成品库页面上的一张卡片：账里那一条，加上它的图还在不在。

    账里那一条（模型、种子、步数都从这儿念）与缩略图那串身份都转发给
    showcase.Card：同一个算法只该有一份，页面上各处照抄它。图在不在是页面
    要的分岔——在就给张缩略图，不在就写一句话，而不是让浏览器去撞一个 404。
    """

    card: showcase.Card
    has_picture: bool
    relative: str

    @property
    def entry(self) -> Entry:
        """账里那一条。"""
        return self.card.entry

    @property
    def thumb(self) -> str:
        """缩略图与登记项共用的那串尾巴。"""
        return self.card.thumb


@dataclass(frozen=True)
class GalleryGroup:
    """成品库上的一天：日期 + 那天的卡片。"""

    day: str
    cards: tuple[CardView, ...]


def _shown(config: Config, recorded: str) -> str:
    """账里记的是仓库相对，页面要的是 output 相对：两种换算只在这儿做一次。"""
    return output_relative(config.paths.output_dir, resolve_recorded(config, recorded))


def _view(config: Config, card: showcase.Card) -> CardView:
    """一张卡片：账里的那一条摊出来，顺手问一句图还在不在。

    relative 是相对 output 的那一截 —— 页面上「打开文件夹」要的是它，账里
    记的却是仓库相对的（换个目录克隆也读得懂），两种换算就在这儿做一次。
    """
    return CardView(
        card=card,
        has_picture=showcase.picture_path(config, card) is not None,
        relative=_shown(config, card.entry.file),
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


@dataclass(frozen=True)
class Prefill:
    """照配方回填出片台的那几栏，外加一句交代。

    台词、种子、底图、重绘强度是真能填回去的；模型与步数出片台没有对应的栏
    （它们在 config 与工作流模板里），所以只念一遍让照配方的人知道这一张当初
    是怎么出的 —— 回填不进去的东西，说清楚比装作没有强。
    """

    prompt: str
    seed: str
    base: str
    denoise: str
    model: str
    steps: str


def _no_such_recipe(raw: str) -> str:
    """账面翻不到这一条时给的那句话：说清是哪一串没找到，别把人晾在空白页上。"""
    return f"配方库里没有 {raw} 这一条 —— 它可能没被保留，或者后来被同号的顶掉了。"


def _prefill(config: Config, raw: str) -> tuple[Prefill | None, str]:
    """把「用同参数重跑」那一串（日期/号/阶段.png）翻成出片台的初值。

    翻不出来不抛：出片台是主页面，宁可开着让它自己出片，也不能因为链接里那
    一串过期就打不开（判据 ③ 要的是"回到出片台"，不是"回不去"）。
    """
    if not raw.strip():
        return None, ""
    parts = Path(raw).parts
    if len(parts) != 3:
        return None, _no_such_recipe(raw)
    day, ident, name = parts
    try:
        card = showcase.find_card(config, day, ident, Path(name).stem)
    except ValueError:
        return None, _no_such_recipe(raw)

    entry = card.entry
    source = entry.source
    denoise = f"{config.generate.denoise:g}"
    base = ""
    if source is not None:
        base = _shown(config, source.image)
        denoise = f"{source.denoise:g}"
    return (
        Prefill(
            prompt=entry.prompt,
            seed=str(entry.seed),
            base=base,
            denoise=denoise,
            model=entry.model,
            steps=str(entry.steps),
        ),
        "",
    )


def _page_extra(page: Page, request: Request, config: Config) -> dict[str, Any]:
    """成品库与出片台各自多要的那几样；另外两页用不着。

    PAGES 管"有哪几页"，这里管"每一页多要什么"。加页时只在这两处各添一条。
    """
    if page.key == "gallery":
        query = request.query_params.get("q", "")
        found = showcase.search(showcase.cards(config), query)
        return {
            "query": query,
            "groups": [
                GalleryGroup(
                    day=group.day, cards=tuple(_view(config, it) for it in group.cards)
                )
                for group in showcase.groups(found)
            ],
            "thumb_mount": THUMB_MOUNT,
            "record_mount": RECORD_MOUNT,
            "generate_path": GENERATE_PATH,
        }
    if page.key == "generate":
        fill, error = _prefill(config, request.query_params.get("from", ""))
        return {"fill": fill, "fill_error": error}
    return {}


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
                "default_denoise": f"{config.generate.denoise:g}",
                **_page_extra(page, request, config),
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

    @app.get(f"{THUMB_MOUNT}/{{day}}/{{ident}}/{{name}}", name="thumb")
    def thumb(day: str, ident: str, name: str) -> Any:
        """成品库那张卡片的小图：按（日期/号/阶段.png）回账里找，现取现缩。

        账里没有、或者图被清走了，都是 404 —— 这一页自己不存图，缩不出来就是
        没有。判据要的正是这个：配方还在、图不在了，页面照旧摊得开。
        """
        try:
            card = showcase.find_card(config, day, ident, Path(name).stem)
        except ValueError:
            return Response(status_code=404)
        path = showcase.picture_path(config, card)
        if path is None:
            return Response(status_code=404)
        try:
            raw = showcase.thumb_bytes(path)
        except ValueError:
            return Response(status_code=404)
        return Response(content=raw, media_type="image/png")

    @app.get(f"{RECORD_MOUNT}/{{day}}/{{ident}}/{{name}}", name="record")
    def record(day: str, ident: str, name: str) -> Any:
        """「查看登记项」：当天那本全量记录里这一条，原样摊成 JSON。

        两本账不是同一本，缺哪一半就说哪一半 —— 配方库那一半还在时，这里
        报的是"当天的记录里没有它"，而不是含混的一句找不到。
        """
        try:
            card = showcase.find_card(config, day, ident, Path(name).stem)
            body = showcase.record_of(config, card)
        except ValueError as exc:
            return JSONResponse({"message": str(exc)}, status_code=404)
        return JSONResponse(body)

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
