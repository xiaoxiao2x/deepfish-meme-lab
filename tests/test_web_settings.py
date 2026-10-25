'''设置页那一层（M5.5）走真 HTTP：四组设置各存一次，地址能当场试一次连接。

三条接缝里 probe 是这一页新加的那一个 —— 测试不让它真去连引擎；opener 用不上
（设置页不弹文件夹）。唯一真发出去的一次是「存下的地址立刻生效」：地址填在不会
有人听的端口上，要的正是那一次被拒。判据是"页面上点得动"而不是
"路由表里有"：POST 之后 local.toml 里真写了、页面再打开时显示的是新值，
而 default.toml 一个字节没动。
'''

from __future__ import annotations

import json
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import uvicorn

from lab.assets import load_index
from lab.config import Config, load_config
from lab.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]

TEMPLATES = ("fish-txt2img.json", "fish-img2img.json", "fish-sticker.json")


class RecordingProbe:
    '''假探测：记下问了哪个地址，回一句安排好的话。'''

    def __init__(self, *, ok: bool = True, detail: str = "") -> None:
        self.calls: list[tuple[str, int, float]] = []
        self.ok = ok
        self.detail = detail

    def __call__(self, host: str, port: int, timeout: float) -> tuple[bool, str]:
        self.calls.append((host, port, timeout))
        return self.ok, self.detail or f"{host}:{port} · ComfyUI 0.42.0"


def _local_text(roots: list[Path], theme: str = "light") -> str:
    '''一份像真本机配置的 local.toml：素材根目录是多行数组，主题另起一段。'''
    body = "".join('  "' + str(root) + '",' + "\n" for root in roots)
    return (
        "# 本机配置 —— 不进 git。\n"
        "\n[paths]\n"
        "# 素材池：只读引用\n"
        "asset_roots = [\n"
        + body
        + "]\n"
        "\n[ui]\n"
        'theme = "' + theme + '"\n'
    )


def _image(path: Path, color: tuple[int, int, int]) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (24, 24), color).save(path, format="PNG")
    return path


def _cfg(tmp_path: Path, *, roots: list[Path], theme: str = "light") -> Config:
    '''一个临时仓库：配置两份、模板三份、素材根目录由调用方摆。'''
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "default.toml").write_text(
        (REPO_ROOT / "config" / "default.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (config_dir / "local.toml").write_text(_local_text(roots, theme), encoding="utf-8")
    workflows = tmp_path / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    for name in TEMPLATES:
        (workflows / name).write_text(
            (REPO_ROOT / "workflows" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    return load_config(tmp_path)


def _local(tmp_path: Path) -> dict:
    return tomllib.loads((tmp_path / "config" / "local.toml").read_text(encoding="utf-8"))


@contextmanager
def run_server(app: Any) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
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


def _receive(request: urllib.request.Request) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"message": raw}


def post_json(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return _receive(request)


def get_json(url: str) -> tuple[int, dict]:
    return _receive(urllib.request.Request(url))


def get_html(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


# ---------- 存下的地址立刻就是状态条要问的那一个 ----------


def test_a_saved_address_is_what_the_engine_route_calls(tmp_path: Path) -> None:
    """判据 ⑤ 的另一半：写回之后，出片台那盏灯问的就是新地址。

    地址填在 127.0.0.1:1 —— 不会有人听，连接当场被拒。这一问真发出去、真被拒，
    回的话里带着新地址，正好证明它读的是刚存下的那份配置，不是启动时的那一份。
    """
    cfg = _cfg(tmp_path, roots=[])
    app = create_app(cfg)
    with run_server(app) as base:
        status, saved = post_json(base + "/api/settings/engine", {"address": "127.0.0.1:1"})
        assert status == 200
        assert saved["address"] == "127.0.0.1:1"
        status, live = get_json(base + "/api/engine")
        assert status == 200
        assert live["ok"] is False
        assert "127.0.0.1:1" in live["message"]


# ---------- 页面上的现状 ----------


def test_the_settings_page_shows_what_is_configured_now(tmp_path: Path) -> None:
    root = tmp_path / "one"
    _image(root / "a.png", (200, 30, 30))
    cfg = _cfg(tmp_path, roots=[root])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, text = get_html(f"{base}/settings")

    assert status == 200
    assert 'data-theme="light"' in text
    assert 'value="127.0.0.1:8188"' in text
    assert 'value="fish-txt2img.json" selected' in text
    assert str(root) in text
    assert f'value="{cfg.paths.output_dir}"' in text
    assert 'id="theme"' in text


def test_the_app_registers_the_settings_routes(tmp_path: Path) -> None:
    app = create_app(_cfg(tmp_path, roots=[]), probe=RecordingProbe())

    names = {getattr(route, "name", "") for route in app.routes}

    wanted = {
        "settings-probe",
        "settings-engine",
        "settings-workflow",
        "settings-paths",
        "settings-theme",
    }
    assert wanted <= names


# ---------- 引擎地址与测试连接 ----------


def test_saving_the_engine_address_writes_local_and_rereads(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    before = (tmp_path / "config" / "default.toml").read_bytes()

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(f"{base}/api/settings/engine", {"address": "http://127.0.0.1:9999/"})
        assert status == 200
        assert body["address"] == "127.0.0.1:9999"
        assert 'value="127.0.0.1:9999"' in get_html(f"{base}/settings")[1]

    assert _local(tmp_path)["comfy"] == {"host": "127.0.0.1", "port": 9999}
    assert (tmp_path / "config" / "default.toml").read_bytes() == before


def test_the_probe_endpoint_asks_about_the_address_in_the_box(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    probe = RecordingProbe()

    with run_server(create_app(cfg, probe=probe)) as base:
        status, body = get_json(f"{base}/api/settings/probe?address=127.0.0.1:9999")

    assert status == 200
    assert body["ok"] is True
    assert body["address"] == "127.0.0.1:9999"
    assert "ComfyUI" in body["detail"]
    assert probe.calls == [("127.0.0.1", 9999, cfg.comfy.probe_timeout)]


def test_the_probe_endpoint_fills_in_the_port_we_already_have(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    probe = RecordingProbe()

    with run_server(create_app(cfg, probe=probe)) as base:
        _status, body = get_json(f"{base}/api/settings/probe?address=box.local")

    assert body["address"] == "box.local:8188"
    assert probe.calls == [("box.local", 8188, cfg.comfy.probe_timeout)]


def test_the_probe_endpoint_says_so_when_it_cannot_connect(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    detail = "连不上 127.0.0.1:9999 —— 请先启动 ComfyUI（conda activate comfyui && python main.py）"
    probe = RecordingProbe(ok=False, detail=detail)

    with run_server(create_app(cfg, probe=probe)) as base:
        status, body = get_json(f"{base}/api/settings/probe?address=127.0.0.1:9999")

    assert status == 200
    assert body["ok"] is False
    assert body["detail"] == detail


def test_the_probe_endpoint_refuses_an_address_that_makes_no_sense(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = get_json(f"{base}/api/settings/probe?address=127.0.0.1:abc")

    assert status == 400
    assert "——" in body["message"]


def test_an_address_that_makes_no_sense_is_a_400_and_writes_nothing(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    path = tmp_path / "config" / "local.toml"
    before = path.read_text(encoding="utf-8")

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(f"{base}/api/settings/engine", {"address": "ftp://127.0.0.1:8188"})

    assert status == 400
    assert "——" in body["message"]
    assert path.read_text(encoding="utf-8") == before


# ---------- 默认工作流 ----------


def test_saving_the_workflow_changes_the_default(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(f"{base}/api/settings/workflow", {"name": "fish-img2img.json"})
        assert status == 200
        assert body["name"] == "fish-img2img.json"
        assert 'value="fish-img2img.json" selected' in get_html(f"{base}/settings")[1]

    assert _local(tmp_path)["workflows"]["default"] == "fish-img2img.json"


def test_an_unknown_workflow_is_a_400(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])
    path = tmp_path / "config" / "local.toml"
    before = path.read_text(encoding="utf-8")

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(f"{base}/api/settings/workflow", {"name": "nope.json"})

    assert status == 400
    assert "nope.json" in body["message"]
    assert path.read_text(encoding="utf-8") == before


# ---------- 路径：改完素材库认得新目录 ----------


def test_saving_the_paths_rescans_so_the_asset_page_sees_the_new_roots(tmp_path: Path) -> None:
    root_a = tmp_path / "one"
    root_b = tmp_path / "two"
    _image(root_a / "a.png", (200, 30, 30))
    _image(root_b / "b.png", (30, 30, 200))
    cfg = _cfg(tmp_path, roots=[root_a])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(
            f"{base}/api/settings/paths",
            {"asset_roots": [str(root_a)], "output_dir": "output"},
        )
        assert status == 200
        assert body["warning"] == ""
        first = load_index(cfg.paths.asset_index)
        assert [str(root) for root in first.roots] == [str(root_a)]
        assert first.count == 1

        status, body = post_json(
            f"{base}/api/settings/paths",
            {"asset_roots": [str(root_b), str(root_b) + " "], "output_dir": str(tmp_path / "out2")},
        )
        assert status == 200
        assert body["asset_roots"] == [str(root_b)]
        again = load_index(cfg.paths.asset_index)
        assert [str(root) for root in again.roots] == [str(root_b)]
        assert again.count == 1

        status, text = get_html(f"{base}/assets")

    assert status == 200
    assert "index-error" not in text
    assert "b.png" in text
    assert "a.png" not in text
    assert _local(tmp_path)["paths"]["asset_roots"] == [str(root_b)]
    assert _local(tmp_path)["paths"]["output_dir"] == str(tmp_path / "out2")


def test_an_empty_output_dir_is_a_400(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(
            f"{base}/api/settings/paths",
            {"asset_roots": [str(tmp_path / "one")], "output_dir": "   "},
        )

    assert status == 400
    assert "输出目录" in body["message"]


# ---------- 主题明暗 ----------


def test_saving_the_theme_changes_the_page(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        assert 'data-theme="light"' in get_html(f"{base}/settings")[1]
        status, body = post_json(f"{base}/api/settings/theme", {"theme": "dark"})
        assert status == 200
        assert body["theme"] == "dark"
        assert 'data-theme="dark"' in get_html(f"{base}/settings")[1]

    assert _local(tmp_path)["ui"]["theme"] == "dark"


def test_an_unknown_theme_is_a_400(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, roots=[])

    with run_server(create_app(cfg, probe=RecordingProbe())) as base:
        status, body = post_json(f"{base}/api/settings/theme", {"theme": "midnight"})

    assert status == 400
    assert "midnight" in body["message"]
