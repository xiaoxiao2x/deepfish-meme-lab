"""体检的行为：五项检查各自说真话。

这组测试刻意不 mock 任何模块 —— 引擎探测走真 HTTP（对着一个真的会响的
极小服务，或一个真的没人监听的端口），文件检查对着真的 tmp_path 目录。
测的是行为，不是接线。
"""

import http.server
import json
import socket
import threading
import tomllib
from contextlib import contextmanager
from pathlib import Path

from lab.cli import main
from lab.config import load_config
from lab.doctor import GB, Status, run_doctor

DEFAULT_TOML = """
[comfy]
host = "127.0.0.1"
port = 8188
timeout = 600
probe_timeout = 3
root = ""
required_nodes = ["ComfyUI-Manager", "ComfyUI-RMBG", "ComfyUI-KJNodes"]

[[comfy.weights]]
file = "models/diffusion_models/klein-base.safetensors"
size_gb = 4.0
note = "Klein 4B base"

[[comfy.weights]]
file = "models/text_encoders/qwen_3_4b.safetensors"
size_gb = 8.0
note = "文本编码器"

[paths]
asset_roots = []
output_dir = "output"
asset_index = "assets/index.local.json"

[workflows]
default = "fish-txt2img.json"

[generate]
count = 4
steps = 20
cfg = 5.0
width = 1024
height = 1024
denoise = 0.55
prompt_prefix = ""
negative = ""
"""


class _SilentHandler(http.server.BaseHTTPRequestHandler):
    """把 access log 咽掉，测试输出才干净。"""

    def log_message(self, *args):
        pass


@contextmanager
def _server(handler_cls):
    """起一个真的会响 HTTP 的极小服务，交出它的端口。"""

    class Server(http.server.HTTPServer):
        allow_reuse_address = True

    srv = Server(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


@contextmanager
def engine_answering(version: str = "0.34.0"):
    """一个真的会响 HTTP 的极小服务，冒充引擎的 /system_stats。"""

    class Handler(_SilentHandler):
        def do_GET(self):
            if self.path == "/system_stats":
                body = json.dumps({"system": {"comfyui_version": version}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

    with _server(Handler) as port:
        yield port


@contextmanager
def impostor_answering(code: int = 500):
    """端口开着，但对面不是 ComfyUI（比如别的服务占了 8188）。"""

    class Handler(_SilentHandler):
        def do_GET(self):
            self.send_error(code)

    with _server(Handler) as port:
        yield port


def dead_port() -> int:
    """拿一个刚刚被释放的端口 —— 短时间内没人会监听它。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_config(
    tmp_path: Path,
    *,
    comfy_root: Path | str | None = None,
    output_dir: str | None = None,
    asset_roots: list[Path] | None = None,
    port: int = 8188,
):
    cfgdir = tmp_path / "config"
    cfgdir.mkdir(parents=True, exist_ok=True)
    (cfgdir / "default.toml").write_text(DEFAULT_TOML, encoding="utf-8")

    lines = ["[comfy]", f"port = {port}"]
    if comfy_root is not None:
        lines.append(f'root = "{comfy_root}"')
    lines += ["", "[paths]"]
    if output_dir is not None:
        lines.append(f'output_dir = "{output_dir}"')
    if asset_roots is not None:
        joined = ", ".join(f'"{p}"' for p in asset_roots)
        lines.append(f"asset_roots = [{joined}]")
    (cfgdir / "local.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return load_config(tmp_path)


def checks(config) -> dict:
    return {r.key: r for r in run_doctor(config)}


def declared_weights() -> dict:
    """DEFAULT_TOML 里声明的权重清单：相对路径 → 标称 GB。"""
    return {w["file"]: w["size_gb"] for w in tomllib.loads(DEFAULT_TOML)["comfy"]["weights"]}


def build_engine_root(tmp_path: Path, *,
                      weights: tuple | None = None,
                      nodes: tuple = ("ComfyUI-Manager", "ComfyUI-RMBG", "ComfyUI-KJNodes"),
                      scale: float = 1.0) -> Path:
    """按标称大小造权重文件（稀疏文件，占位不占盘），外加 custom_nodes 目录。"""
    declared = declared_weights()
    wanted = weights if weights is not None else tuple(declared)

    root = tmp_path / "ComfyUI"
    for rel in wanted:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.truncate(int(declared.get(rel, 1.0) * scale * GB))
    for name in nodes:
        (root / "custom_nodes" / name).mkdir(parents=True, exist_ok=True)
    return root


# ── 引擎连通 ──────────────────────────────────────────────────────────


def test_engine_down_is_a_human_sentence_not_a_traceback(tmp_path):
    cfg = make_config(tmp_path, port=dead_port())

    engine = checks(cfg)["engine"]

    assert engine.status is Status.FAIL
    assert "连不上" in engine.detail
    assert "ComfyUI" in engine.detail


def test_engine_up_reports_its_version(tmp_path):
    with engine_answering("0.34.0") as port:
        cfg = make_config(tmp_path, port=port)

        engine = checks(cfg)["engine"]

    assert engine.status is Status.OK
    assert "0.34.0" in engine.detail


def test_engine_answering_garbage_is_reported_as_such(tmp_path):
    """端口开着但不是 ComfyUI（比如别的服务占了 8188）。"""
    with impostor_answering(500) as port:
        cfg = make_config(tmp_path, port=port)

        engine = checks(cfg)["engine"]

    assert engine.status is Status.FAIL
    assert "500" in engine.detail


# ── 权重 ──────────────────────────────────────────────────────────────


def test_weights_ok_when_all_present(tmp_path):
    root = build_engine_root(tmp_path)
    cfg = make_config(tmp_path, comfy_root=root, port=dead_port())

    weights = checks(cfg)["weights"]

    assert weights.status is Status.OK
    assert "2" in weights.detail


def test_absent_weight_is_named(tmp_path):
    root = build_engine_root(tmp_path, weights=("models/diffusion_models/klein-base.safetensors",))
    cfg = make_config(tmp_path, comfy_root=root, port=dead_port())

    weights = checks(cfg)["weights"]

    assert weights.status is Status.FAIL
    assert "qwen_3_4b.safetensors" in weights.detail


def test_truncated_download_is_caught(tmp_path):
    """半截文件比文件缺失更阴险 —— 引擎会在加载时才炸。"""
    root = build_engine_root(tmp_path, scale=0.001)  # 千分之一，远小于标称 4 GB
    cfg = make_config(tmp_path, comfy_root=root, port=dead_port())

    weights = checks(cfg)["weights"]

    assert weights.status is Status.FAIL
    assert "klein-base" in weights.detail


def test_engine_root_unset_says_so_instead_of_crashing(tmp_path):
    cfg = make_config(tmp_path, port=dead_port())  # 没配 comfy.root

    result = checks(cfg)

    assert result["weights"].status is Status.WARN
    assert "comfy.root" in result["weights"].detail
    assert result["nodes"].status is Status.WARN
    assert "comfy.root" in result["nodes"].detail


def test_engine_root_pointing_nowhere_is_reported_as_such(tmp_path):
    """写错了路径时别逐条报「缺 4 个权重」—— 那会把结论指向错误的地方。"""
    cfg = make_config(tmp_path, comfy_root=tmp_path / "没这个目录", port=dead_port())

    result = checks(cfg)

    assert result["weights"].status is Status.FAIL
    assert "comfy.root" in result["weights"].detail
    assert "不存在" in result["weights"].detail
    assert result["nodes"].status is Status.FAIL
    assert "不存在" in result["nodes"].detail


# ── custom_nodes ──────────────────────────────────────────────────────


def test_missing_node_is_named(tmp_path):
    root = build_engine_root(tmp_path, nodes=("ComfyUI-Manager",))
    cfg = make_config(tmp_path, comfy_root=root, port=dead_port())

    nodes = checks(cfg)["nodes"]

    assert nodes.status is Status.FAIL
    assert "ComfyUI-RMBG" in nodes.detail
    assert "ComfyUI-KJNodes" in nodes.detail


# ── 素材 ──────────────────────────────────────────────────────────────


def test_asset_roots_unset_warns_rather_than_fails(tmp_path):
    cfg = make_config(tmp_path, port=dead_port())

    assets = checks(cfg)["assets"]

    assert assets.status is Status.WARN
    assert "素材" in assets.detail


def test_asset_root_reachable(tmp_path):
    pool = tmp_path / "素材池"
    pool.mkdir()
    (pool / "a.png").write_bytes(b"x")
    (pool / "b.webp").write_bytes(b"x")
    cfg = make_config(tmp_path, asset_roots=[pool], port=dead_port())

    assets = checks(cfg)["assets"]

    assert assets.status is Status.OK
    assert "2" in assets.detail


def test_configured_but_absent_asset_root_fails(tmp_path):
    cfg = make_config(tmp_path, asset_roots=[tmp_path / "nope"], port=dead_port())

    assets = checks(cfg)["assets"]

    assert assets.status is Status.FAIL
    assert "nope" in assets.detail


# ── 输出目录 ──────────────────────────────────────────────────────────


def test_output_dir_is_created_and_reported(tmp_path):
    cfg = make_config(tmp_path, port=dead_port())

    output = checks(cfg)["output"]

    assert output.status is Status.OK
    assert (tmp_path / "output").is_dir()


def test_unwritable_output_dir_fails(tmp_path):
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    frozen.chmod(0o500)
    cfg = make_config(tmp_path, output_dir=str(frozen), port=dead_port())
    try:
        output = checks(cfg)["output"]
    finally:
        frozen.chmod(0o700)

    assert output.status is Status.FAIL
    assert "写" in output.detail


def test_write_probe_leaves_nothing_behind(tmp_path):
    """探针是临时文件，自己清理；别在输出目录里留垃圾。"""
    cfg = make_config(tmp_path, port=dead_port())

    checks(cfg)

    assert list((tmp_path / "output").iterdir()) == []


# ── 命令行 ────────────────────────────────────────────────────────────


def test_every_check_has_a_key(tmp_path):
    cfg = make_config(tmp_path, port=dead_port())

    keys = {r.key for r in run_doctor(cfg)}

    assert keys == {"engine", "weights", "nodes", "assets", "output"}


def test_cli_doctor_exits_1_when_something_fails(tmp_path, capsys):
    make_config(tmp_path, port=dead_port())

    rc = main(["doctor"], repo_root=tmp_path)

    assert rc == 1
    assert "体检" in capsys.readouterr().out


def test_cli_exits_nonzero_when_something_is_only_unconfigured(tmp_path, capsys):
    """没配 ≠ 通过。引擎活着但 comfy.root 空着，退出码也得非 0。"""
    with engine_answering() as port:
        make_config(tmp_path, port=port)  # 没配 comfy.root，也没配素材根

        rc = main(["doctor"], repo_root=tmp_path)

    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL" not in out  # 一条 FAIL 都没有……
    assert "WARN" in out  # ……但仍然是「没就绪」


def test_cli_doctor_exits_0_when_everything_is_ready(tmp_path, capsys):
    with engine_answering() as port:
        root = build_engine_root(tmp_path)
        pool = tmp_path / "素材池"
        pool.mkdir()
        make_config(tmp_path, comfy_root=root, asset_roots=[pool], port=port)

        rc = main(["doctor"], repo_root=tmp_path)

    out = capsys.readouterr().out
    assert rc == 0
    assert "FAIL" not in out


def test_cli_without_arguments_prints_usage(tmp_path, capsys):
    make_config(tmp_path, port=dead_port())

    rc = main([], repo_root=tmp_path)

    out = capsys.readouterr().out
    assert rc == 2
    assert "doctor" in out


def test_doctor_runs_on_a_machine_without_pillow(tmp_path, capsys, monkeypatch):
    """体检不该需要 Pillow —— 换一台没装它的机器，`lab doctor` 也得起得来。

    `lab/assets.py` 要 Pillow，所以素材命令是在 `_run_assets` 里才 import 它的。
    这条测试把 PIL 从 import 系统里摘掉、并把 lab.* 从模块缓存里清空重来，
    任何挂在顶层的三方依赖都会在这里现形。
    """
    import builtins
    import sys

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("这条测试假装这台机器没装 Pillow")
        return real_import(name, *args, **kwargs)

    for module in [m for m in sys.modules if m.startswith("lab.")]:
        monkeypatch.delitem(sys.modules, module, raising=False)
    monkeypatch.setattr(builtins, "__import__", blocked)
    make_config(tmp_path, port=dead_port())

    rc = main(["doctor"], repo_root=tmp_path)  # 引擎没起，但绝不该是 ImportError

    assert rc in (0, 1)
    assert "Pillow" not in capsys.readouterr().out
