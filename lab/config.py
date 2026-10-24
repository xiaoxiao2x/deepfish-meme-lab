"""两层配置：default.toml（进 git）+ local.toml（不进 git）。

    config/default.toml   与机器无关的默认值，随代码走
    config/local.toml     本机绝对路径（ComfyUI 装在哪、素材在哪），不随代码走

合并是**深合并**：local 只覆盖它点名的键，段内其它键原样保住。
这样换机器只需要写一个几行的 local.toml，不用把 default 抄一遍。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Config",
    "ConfigError",
    "GenerateConfig",
    "PostprocessConfig",
    "ServerConfig",
    "WorkflowsConfig",
    "load_config",
]


class ConfigError(Exception):
    """配置缺失、缺键、TOML 语法坏了，或某个值根本不合理（字号是 0、颜色拼错）。"""


@dataclass(frozen=True)
class WeightSpec:
    """引擎侧必须存在的一个权重文件。"""

    file: str  # 相对 ComfyUI 根目录
    size_gb: float  # 标称体积，用来抓半截下载
    note: str = ""


@dataclass(frozen=True)
class ComfyConfig:
    host: str
    port: int
    timeout: int  # 单次任务的等待上限
    probe_timeout: int  # 体检探测的等待上限 —— 体检不该等十分钟
    root: Path | None  # ComfyUI 安装目录；没配就是 None
    required_nodes: tuple[str, ...]
    weights: tuple[WeightSpec, ...]

    @property
    def endpoint(self) -> str:
        """引擎的 HTTP 根地址。别在别处现拼 host 和 port。"""
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class PathsConfig:
    output_dir: Path  # 已解析成绝对路径
    asset_roots: tuple[Path, ...]  # 只读引用：不复制、不修改、不移动
    asset_index: Path


@dataclass(frozen=True)
class GenerateConfig:
    """出片的默认业务参数 —— 命令行没给时用它。

    这些是"业务参数"（张数、步数、引导强度、画布），不是节点参数：
    它们由 workflows.py 按标题翻译进模板，CLI 只认这一层。
    """

    count: int
    steps: int
    cfg: float
    width: int
    height: int
    denoise: float
    prompt_prefix: str
    negative: str


@dataclass(frozen=True)
class WorkflowsConfig:
    """模板文件名，相对 workflows/。"""

    default: str  # 文生图
    img2img: str  # 底图复用；#8 之前可能还没有这个文件，所以允许为空
    sticker: str = ""  # 去背；留空 = 这份配置还没接去背


@dataclass(frozen=True)
class PostprocessConfig:
    """加字那一站的外观 —— 这里是它唯一的值来源，命令行不给这几项覆盖选项。

    字体带本机气味（绝对路径），所以默认留空：留空就按 lab/lettering.py 里那张
    常见位置表自动挑，本机实际值写在 config/local.toml。
    """

    font: Path | None  # 已解析成绝对路径；没配就是 None
    font_size: int
    stroke_width: int
    stroke_color: str  # #RRGGBB，描边色
    fill: str  # #RRGGBB，字色


@dataclass(frozen=True)
class ServerConfig:
    """本地界面监听在哪（给 lab serve 用）。

    默认只绑回环地址：这东西没有登录，绑到 0.0.0.0 就等于把它挂在局域网上。
    端口默认 8765 —— 引擎占着 8188，别撞上。
    """

    host: str
    port: int

    @property
    def address(self) -> str:
        """浏览器该打开的那个地址。别在别处现拼 host 与 port。"""
        return f"http://{self.host}:{self.port}"

    def overridden(self, *, host: str | None = None, port: int | None = None) -> ServerConfig:
        """命令行给的那两个盖上来，没给的照原样。

        用一次起一次：给完就有一份完整地址，避免在调用处又拼一遍 host 与 port。
        """
        return ServerConfig(host=host or self.host, port=port or self.port)


@dataclass(frozen=True)
class Config:
    repo_root: Path
    comfy: ComfyConfig
    paths: PathsConfig
    server: ServerConfig
    generate: GenerateConfig
    workflows: WorkflowsConfig
    postprocess: PostprocessConfig


def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"找不到 {path.name}（应在 {path}）") from None
    except IsADirectoryError:
        raise ConfigError(f"{path.name} 是个目录，不是文件") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name} 语法有问题：{exc}") from None


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _require(data: dict, dotted: str):
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(
                f"配置缺键：{dotted}"
                f"（在 config/default.toml 补默认值，或在 config/local.toml 里覆盖）"
            )
        node = node[part]
    return node


def _resolve(repo_root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (repo_root / path)


def load_config(repo_root: Path | str) -> Config:
    """从仓库根目录读两份配置，合成一个冻结的 Config。"""
    root = Path(repo_root).expanduser().resolve()
    config_dir = root / "config"

    data = _read_toml(config_dir / "default.toml")
    local = config_dir / "local.toml"
    if local.is_file():
        data = _deep_merge(data, _read_toml(local))

    comfy_raw = data.get("comfy") or {}
    root_value = comfy_raw.get("root")
    comfy = ComfyConfig(
        host=_require(data, "comfy.host"),
        port=int(_require(data, "comfy.port")),
        timeout=int(_require(data, "comfy.timeout")),
        probe_timeout=int(comfy_raw.get("probe_timeout", 3)),
        root=_resolve(root, root_value) if root_value else None,
        required_nodes=tuple(comfy_raw.get("required_nodes") or ()),
        weights=tuple(
            WeightSpec(
                file=w["file"], size_gb=float(w["size_gb"]), note=w.get("note", "")
            )
            for w in (comfy_raw.get("weights") or ())
        ),
    )

    paths = PathsConfig(
        output_dir=_resolve(root, _require(data, "paths.output_dir")),
        asset_roots=tuple(
            _resolve(root, p) for p in (_require(data, "paths.asset_roots") or ())
        ),
        asset_index=_resolve(root, _require(data, "paths.asset_index")),
    )

    generate = GenerateConfig(
        count=int(_require(data, "generate.count")),
        steps=int(_require(data, "generate.steps")),
        cfg=float(_require(data, "generate.cfg")),
        width=int(_require(data, "generate.width")),
        height=int(_require(data, "generate.height")),
        denoise=float(_require(data, "generate.denoise")),
        prompt_prefix=str((data.get("generate") or {}).get("prompt_prefix") or ""),
        negative=str((data.get("generate") or {}).get("negative") or ""),
    )

    workflows_raw = data.get("workflows") or {}
    workflows = WorkflowsConfig(
        default=str(_require(data, "workflows.default")),
        img2img=str(workflows_raw.get("img2img") or ""),
        sticker=str(workflows_raw.get("sticker") or ""),
    )

    # 界面的地址可以不给：[server] 那一段是空的也照样起得来（用这两个默认值）。
    # 与 postprocess 同一种写法 —— 缺哪一栏就用哪一栏的默认，不必在 local.toml 里抄一遍。
    server_raw = data.get("server") or {}
    server = ServerConfig(
        host=str(server_raw.get("host") or "127.0.0.1"),
        port=int(server_raw.get("port", 8765)),
    )

    postprocess_raw = data.get("postprocess") or {}
    font_value = postprocess_raw.get("font") or ""
    postprocess = PostprocessConfig(
        # 空串 = 没配，让 lettering 自己挑一个常见位置。
        font=_resolve(root, font_value) if font_value else None,
        font_size=int(postprocess_raw.get("font_size", 64)),
        stroke_width=int(postprocess_raw.get("stroke_width", 4)),
        stroke_color=str(postprocess_raw.get("stroke_color") or "#FFFFFF"),
        fill=str(postprocess_raw.get("fill") or "#1B1B1B"),
    )

    return Config(
        repo_root=root,
        comfy=comfy,
        paths=paths,
        server=server,
        generate=generate,
        workflows=workflows,
        postprocess=postprocess,
    )
