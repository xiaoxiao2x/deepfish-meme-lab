"""体检：一次跑完，把环境里缺的东西全说清楚。

为什么它比生成命令更早存在 —— 没有它的时候，每一个环境错误（权重没下完、
节点没装、引擎没起）都要花十分钟去猜。有了它，一条命令给出全部答案。

检查读的是真实状态（HTTP 端口、文件、目录），报的是人话。唯一的例外是
「输出目录」那项：它必须真写一个探针文件才知道能不能写，写完就删。

两种「没成」要分清：

    WARN  没配，所以没查 —— 不算通过（退出码非 0）
    FAIL  查了，确实缺东西
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from . import workflows
from .comfy import EngineClient, EngineError, EngineUnreachable, show_address
from .config import Config
from .contract import problems as template_problems
from .paths import iter_image_files

__all__ = ["CheckResult", "Status", "has_failures", "is_ready", "probe_engine", "run_doctor"]

# 权重体积下限的宽容度。标称值本身是四舍五入过的，所以不能卡死；
# 代价是「下到 96% 断掉」这种半截抓不出来 —— 要精确判断得配 sha256，那是以后的事。
SIZE_TOLERANCE = 0.95
GB = 1000**3

# EngineClient 在引擎回的东西认不出来时给的字面值
UNKNOWN_VERSION = "未知"


class Status(Enum):
    OK = "ok"
    WARN = "warn"  # 没配，所以没法查
    FAIL = "fail"  # 真缺东西


@dataclass(frozen=True)
class CheckResult:
    key: str  # engine / weights / nodes / assets / output
    label: str  # 给人看的名字
    status: Status
    detail: str


# ── 共用零件 ──────────────────────────────────────────────────────────


def _unconfigured(key: str, label: str, what: str) -> CheckResult:
    """统一的「没配，所以没查」出口。"""
    return CheckResult(key, label, Status.WARN, what)


def _engine_root(cfg: Config, key: str, label: str) -> tuple[Path | None, CheckResult | None]:
    """ComfyUI 根目录；用不了时给出原因，别让上层去猜是哪个文件缺了。"""
    root = cfg.comfy.root
    if root is None:
        return None, _unconfigured(
            key, label,
            "还没配 comfy.root —— 在 config/local.toml 写上 ComfyUI 安装目录才能检查",
        )
    if not root.is_dir():
        return None, CheckResult(
            key, label, Status.FAIL, f"comfy.root 指向的目录不存在：{root}"
        )
    return root, None


# ── 引擎 ──────────────────────────────────────────────────────────────


def probe_engine(host: str, port: int, timeout: float) -> tuple[bool, str]:
    """问一次引擎通不通，回 (通不通, 一句人话)。

    体检的那一项与设置页的「测试连接」用的是同一段探测、同一套措辞 ——
    分两份写，第一次改说法就会分家。
    """
    where = show_address(host, port)
    client = EngineClient(host=host, port=port, timeout=timeout)

    try:
        version = client.version()
    except EngineUnreachable:
        return False, (
            f"连不上 {where} —— 请先启动 ComfyUI（conda activate comfyui && python main.py）"
        )
    except EngineError as exc:
        return False, (
            f"{where} 上有服务，但它对 /system_stats 回了：{exc} —— 端口也许被别的程序占着"
        )

    if version == UNKNOWN_VERSION:
        return False, f"{where} 上有服务，但它对 /system_stats 的回应不像 ComfyUI"
    return True, f"{where} · ComfyUI {version}"


def check_engine(cfg: Config) -> CheckResult:
    key, label = "engine", "引擎连通"
    # 超时换成 probe_timeout 的客户端 —— 体检不该等十分钟
    ok, detail = probe_engine(cfg.comfy.host, cfg.comfy.port, cfg.comfy.probe_timeout)
    return CheckResult(key, label, Status.OK if ok else Status.FAIL, detail)


# ── 权重 ──────────────────────────────────────────────────────────────


def check_weights(cfg: Config) -> CheckResult:
    key, label = "weights", "生图权重"
    root, problem = _engine_root(cfg, key, label)
    if problem is not None:
        return problem
    assert root is not None  # 契约：没有 problem 就一定给了 root
    if not cfg.comfy.weights:
        return _unconfigured(key, label, "默认配置里没有权重清单")

    missing: list[str] = []
    truncated: list[str] = []
    for spec in cfg.comfy.weights:
        path = root / spec.file
        name = Path(spec.file).name
        if not path.is_file():
            missing.append(name)
            continue
        floor = int(spec.size_gb * SIZE_TOLERANCE * GB)
        actual = path.stat().st_size
        if actual < floor:
            truncated.append(f"{name}（{actual / GB:.2f} GB，标称 {spec.size_gb} GB）")

    if missing or truncated:
        parts = []
        if missing:
            parts.append("缺 " + "、".join(missing))
        if truncated:
            parts.append("体积不足 " + "、".join(truncated))
        return CheckResult(
            key, label, Status.FAIL, "；".join(parts) + f"（都在 {root} 下）"
        )

    total = sum(spec.size_gb for spec in cfg.comfy.weights)
    return CheckResult(
        key, label, Status.OK, f"{len(cfg.comfy.weights)} 个文件在位，共约 {total:.1f} GB"
    )


# ── custom_nodes ──────────────────────────────────────────────────────


def check_nodes(cfg: Config) -> CheckResult:
    key, label = "nodes", "节点包"
    root, problem = _engine_root(cfg, key, label)
    if problem is not None:
        return problem
    assert root is not None  # 契约：没有 problem 就一定给了 root
    if not cfg.comfy.required_nodes:
        return _unconfigured(key, label, "默认配置里没有节点清单")

    node_dir = root / "custom_nodes"
    missing = [name for name in cfg.comfy.required_nodes if not (node_dir / name).is_dir()]
    if missing:
        return CheckResult(
            key, label, Status.FAIL, "缺 " + "、".join(missing) + f"（在 {node_dir}）"
        )
    # 目录在 ≠ 能加载：pip 依赖没装时 ComfyUI 会在启动日志里报错。
    # 那是引擎自己的事，体检只能保证文件确实放进去了。
    return CheckResult(
        key, label, Status.OK,
        f"{len(cfg.comfy.required_nodes)} 个包已放置（能否加载看引擎启动日志）",
    )


# ── 模板契约 ──────────────────────────────────────────────────────────


def declared_templates(cfg: Config) -> list[str]:
    """配置里点过名的模板文件（去重、保序）—— 只体检真会被用到的那几份。"""
    names: list[str] = []
    for name in (cfg.workflows.default, cfg.workflows.img2img, cfg.workflows.sticker):
        if name and name not in names:
            names.append(name)
    return names


def check_templates(cfg: Config, client: EngineClient | None = None) -> CheckResult:
    """模板与引擎的节点清单对账（lab.contract）。

    这是唯一一项「模板文件没改、但引擎那边变了」的检查：节点包装错、节点升级改名、
    模型文件没放进引擎的目录，都只在这对账里现形。引擎不在就跳过（WARN，不是 FAIL）
    —— 引擎本身是单独一项检查，不必在这里重复报一遍。

    client 可以注入：测试拿 StubEngine 顶替真引擎。
    """
    key, label = "templates", "模板契约"
    names = declared_templates(cfg)
    if not names:
        return _unconfigured(key, label, "默认配置里没有模板清单")

    where = show_address(cfg.comfy.host, cfg.comfy.port)
    if client is None:
        # 和 check_engine 一样用探测超时：体检不该等十分钟
        client = EngineClient(
            host=cfg.comfy.host, port=cfg.comfy.port, timeout=cfg.comfy.probe_timeout
        )
    try:
        object_info = client.object_info()
    except EngineUnreachable:
        return CheckResult(
            key, label, Status.WARN,
            f"连不上 {where}，没法核对模板里的节点 —— 引擎起来再跑一次",
        )
    except EngineError as exc:
        return CheckResult(
            key, label, Status.WARN, f"{where} 没给出节点清单：{exc} —— 跳过核对"
        )

    found: list[str] = []
    for name in names:
        try:
            workflow = workflows.load(cfg.repo_root / "workflows" / name)
        except workflows.WorkflowError as exc:
            found.append(str(exc))
            continue
        found.extend(template_problems(workflow, object_info, name=name))

    if found:
        # 一条 detail 里塞不下整份问题清单，报前三条、其余报个数
        head = "；".join(found[:3])
        more = f"（还有 {len(found) - 3} 处）" if len(found) > 3 else ""
        return CheckResult(key, label, Status.FAIL, head + more)
    return CheckResult(
        key, label, Status.OK,
        f"{len(names)} 份模板对得上（引擎 {len(object_info)} 个节点）",
    )


# ── 素材 ──────────────────────────────────────────────────────────────


def check_assets(cfg: Config) -> CheckResult:
    key, label = "assets", "素材池"
    roots = cfg.paths.asset_roots
    if not roots:
        return _unconfigured(
            key, label,
            "还没配素材根目录 —— 在 config/local.toml 的 paths.asset_roots 里写上"
            "（素材只读引用，不会被动）",
        )

    missing = [str(path) for path in roots if not path.is_dir()]
    if missing:
        return CheckResult(key, label, Status.FAIL, "这些路径不存在：" + "、".join(missing))

    # 用素材层那同一张后缀表与排除规则 —— 两份清单迟早一边加、一边忘
    count = sum(1 for root in roots for _ in iter_image_files(root))
    return CheckResult(key, label, Status.OK, f"{len(roots)} 个根目录可达，共 {count} 张图")


# ── 输出目录 ──────────────────────────────────────────────────────────


def check_output(cfg: Config) -> CheckResult:
    key, label = "output", "输出目录"
    out = cfg.paths.output_dir

    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return CheckResult(key, label, Status.FAIL, f"建不出 {out}：{exc.strerror or exc}")

    try:
        # 探针交给 tempfile 清理，免得自己删在半路留下垃圾、还把「删不掉」误报成「写不进去」
        with tempfile.NamedTemporaryFile(dir=out, prefix=".lab-write-probe-"):
            pass
    except OSError:
        return CheckResult(key, label, Status.FAIL, f"{out} 建得出来，但写不进去")
    return CheckResult(key, label, Status.OK, str(out))


CHECKS = (
    check_engine,
    check_weights,
    check_nodes,
    check_templates,
    check_assets,
    check_output,
)


def run_doctor(cfg: Config) -> list[CheckResult]:
    """跑完全部检查。顺序是有意的：引擎最外层，输出目录最里层。"""
    return [check(cfg) for check in CHECKS]


def has_failures(results) -> bool:
    return any(result.status is Status.FAIL for result in results)


def is_ready(results) -> bool:
    """全绿才算就绪 —— WARN 是「没查」，不是通过。"""
    return all(result.status is Status.OK for result in results)
