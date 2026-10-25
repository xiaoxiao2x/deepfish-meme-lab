"""本地界面（M5.1）：四个页面、导航、静态资源。

真起一次服务再请求，而不是只比路由表：判据第一条是「起得来」—— 只断言
`app.routes` 里有个 path，证明不了 uvicorn 起得来、页面画得出来。

页面里该填什么（出片台的参数面板与结果网格、素材库的瀑布流……）是 M5.2–M5.5 的
事，这里只守骨架：四个页面都在、都能从任何一页跳到任何一页、样式由服务自己发。
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn

from lab.config import load_config
from lab.web import PAGES, STATIC_DIR, TEMPLATES_DIR, create_app, templates_env

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 导航上的顺序就是页面的顺序，四个一个不能少（design.md §9.2）。
NAV = ["出片台", "素材库", "成品库", "设置"]


def _cfg():
    return load_config(REPO_ROOT)


@contextmanager
def _serving() -> Iterator[str]:
    """起一次服务，给出它的地址；出了这个 with 就停掉。"""
    app = create_app(_cfg())
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("本地界面没能在 15 秒内起来")
    assert server.servers, "服务起来了但没监听"
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    """一个模块共用一个服务：起一次约半秒，每张用例各起一次纯属浪费。"""
    with _serving() as url:
        yield url


def _get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _free_port() -> int:
    """要一个当下空着的端口：先绑 0 问系统要，再放掉。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _shutdown(proc: subprocess.Popen[str]) -> str:
    """把子进程收掉，顺带把它说过的话拿回来 —— CI 上真红了，诊断全靠这一段。"""
    proc.terminate()
    try:
        return proc.communicate(timeout=15)[0] or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.communicate()[0] or ""


def test_pages_are_the_four_in_nav_order():
    assert [page.title for page in PAGES] == NAV
    assert PAGES[0].path == "/"  # 首页就是出片台（design.md §9.2）
    assert len({page.path for page in PAGES}) == len(PAGES)


def test_every_page_has_its_own_template():
    names = {page.template for page in PAGES}
    assert len(names) == len(PAGES)  # 四页各一份，不是四页共用一个
    for page in PAGES:
        assert (TEMPLATES_DIR / page.template).is_file(), page.template


def test_the_stylesheet_ships_with_the_package():
    """零构建：样式表跟着包走，不是构建出来再塞进去的。"""
    sheet = STATIC_DIR / "app.css"
    assert sheet.is_file()
    assert sheet.read_text(encoding="utf-8").strip()


def test_app_registers_one_route_per_page():
    paths = {getattr(route, "path", "") for route in create_app(_cfg()).routes}
    for page in PAGES:
        assert page.path in paths
    assert "/static" in paths


def test_every_page_answers_200_and_links_to_every_page(base_url):
    for page in PAGES:
        status, html = _get(base_url + page.path)
        assert status == 200, page.path
        for other in PAGES:
            assert f'href="{other.path}"' in html, f"{page.path} 里没有到 {other.path} 的路"
        assert page.title in html


def test_the_current_page_is_marked_in_the_nav(base_url):
    for page in PAGES:
        _, html = _get(base_url + page.path)
        marked = f'href="{page.path}" aria-current="page"'
        assert marked in html, f"{page.path} 没在导航里标出当前位置"


def test_the_stylesheet_is_served_by_the_app(base_url):
    status, body = _get(base_url + "/static/app.css")
    assert status == 200
    assert "{" in body  # 真是一份样式表，不是空壳


def test_a_page_that_does_not_exist_is_404(base_url):
    status, _ = _get(base_url + "/nope")
    assert status == 404


def test_the_repo_has_no_frontend_build_chain():
    """零构建的另一半：仓库里不该有 node_modules、锁文件或打包器配置。"""
    junk = [
        name
        for name in (
            "node_modules",
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "vite.config.js",
            "webpack.config.js",
            "esbuild.config.js",
        )
        if (REPO_ROOT / name).exists()
    ]

    assert junk == [], f"界面是零构建的，仓库里不该有这些：{junk}"


def test_lab_serve_prints_a_clickable_address():
    """判据第一条走的就是这条路：`python -m lab serve` 起得来、终端上有可点的地址。

    上面那几条用的是 create_app —— 那证明不了 `lab serve` 这条路通。
    """
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "lab", "serve", "--port", str(port)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.1)
        else:
            raise AssertionError(f"lab serve 没能在 20 秒内起来：\n{_shutdown(proc)}")

        assert proc.stdout is not None
        line = proc.stdout.readline()  # 地址那行早就 flush 出来了，这里不会挂住
        assert line.startswith("工作台起在 http://127.0.0.1:")
        assert f":{port} ——" in line
    finally:
        _shutdown(proc)


def test_the_prefixes_come_from_the_server_not_the_page(base_url):
    """前端不自己写死 /api 与 /images：常量注进 body 的 data-*，页面照读。"""
    status, html = _get(base_url + "/")
    assert status == 200
    assert 'data-api="/api"' in html
    assert 'data-images="/images"' in html


def test_the_batch_size_selector_starts_where_the_config_says(base_url):
    """一次几张只有一处来源：config 的 [generate].count。"""
    status, html = _get(base_url + "/")
    assert status == 200
    count = _cfg().generate.count
    assert f'value="{count}" selected' in html


def test_a_page_that_asks_for_a_variable_nobody_passed_says_so():
    """模板环境是严格模式：写错变量名当场报，不许画成一处看不出来的空缺。

    Jinja2 默认对未定义变量留空 —— 页面上就是少了一行字，服务端日志里什么都没有，
    于是「为什么这里没显示」只能靠猜。这条守的是那个开关本身：四个页面能画出来
    （上面那几条）说明它们没有一个未定义变量，而这一条说明真出了那个错会被喊停。
    """
    import jinja2

    env = templates_env()
    with pytest.raises(jinja2.UndefinedError):
        env.from_string("{{ nobody_passed_this }}").render()
