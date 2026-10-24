"""本地界面：把工作台已经能做的事端到浏览器。

四个页面（出片台 / 素材库 / 成品库 / 设置），每页一个路由、一份 Jinja2 页面；
样式跟着包走，没有构建步骤 —— 改完刷新浏览器就看见（design.md §9.1）。

这一层**不做判断**：它把已经能跑的那些动作摆到页面上，自己不新长一套逻辑。
真正的出片动作在 M5.2 接上；眼下四个页面还是空壳，导航先通了。

地址取自 [server]（本机实际值可以写在 config/local.toml），命令行给的覆盖它。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Config

__all__ = ["PAGES", "STATIC_DIR", "TEMPLATES_DIR", "Page", "create_app", "serve"]

SITE_NAME = "鱼片加工厂"

#: 页面本身（HTML 与 CSS）跟代码放一起：它们是这个包的一部分，不是仓库外的另一摊资源。
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

#: 浏览器那侧取这些文件的路由前缀。
STATIC_MOUNT = "/static"


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
            },
        )

    return handler


def create_app(config: Config) -> FastAPI:
    """把这个仓库装进一个 app。`lab serve` 与测试都从这儿进来。"""
    app = FastAPI(title=SITE_NAME, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount(STATIC_MOUNT, StaticFiles(directory=str(STATIC_DIR)), name="static")
    for page in PAGES:
        app.get(page.path, response_class=HTMLResponse, name=page.key)(
            _page_handler(page, templates, config)
        )
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
