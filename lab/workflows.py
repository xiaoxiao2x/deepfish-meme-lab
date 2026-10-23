"""工作流模板的加载与参数注入。

模板是 **API 格式** JSON（`/prompt` 直接吃这个结构），存在 `workflows/` 下。

注入认**标题**不认节点 ID（design.md §6.3）：节点 ID 会在重排工作流时变化，
标题不会。代价是标题写错一个字就静默失效，所以这里对「找不到标题」一律报错，
不做"没找到就跳过"——静默失效比报错难查十倍。

本模块不碰网络、不碰文件系统以外的东西，纯函数进出，方便钉死行为。
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "REQUIRED_TITLES",
    "TITLE_CFG",
    "TITLE_DENOISE",
    "TITLE_INIT_IMAGE",
    "TITLE_NEGATIVE",
    "TITLE_PROMPT",
    "TITLE_SAMPLER",
    "TITLE_SEED",
    "TITLE_SIZE",
    "TITLE_STEPS",
    "GenSpec",
    "WorkflowError",
    "apply",
    "find_by_title",
    "load",
    "missing_titles",
]


#: design.md §6.3 的约定标题集合 + 本票补的「引导强度」。
#: CFG 在 Flux2 工作流里挂在 CFGGuider 上，有自己的语义，不该塞进「步数」。
TITLE_PROMPT = "提示词"
TITLE_NEGATIVE = "负面词"
TITLE_SAMPLER = "采样器"
TITLE_SEED = "种子"
TITLE_STEPS = "步数"
TITLE_SIZE = "尺寸"
TITLE_CFG = "引导强度"

#: 只有底图模板（img2img）才有的两件东西。它们不进 REQUIRED_TITLES ——
#: 文生图模板里没有「底图」是正常的；该报错的是"用错了模板"。
TITLE_INIT_IMAGE = "底图"
TITLE_DENOISE = "重绘强度"

#: 注入必须有、缺一个就报错的标题。「采样器」是模板的选择而非业务参数，
#: 所以它不在这里 —— 但模板结构测试会单独要求它存在。
REQUIRED_TITLES = (
    TITLE_PROMPT,
    TITLE_NEGATIVE,
    TITLE_SEED,
    TITLE_STEPS,
    TITLE_SIZE,
    TITLE_CFG,
)


class WorkflowError(Exception):
    """模板读不出来、缺标题，或者注入后仍不合法。"""


@dataclass(frozen=True)
class GenSpec:
    """一次出片的业务参数。全是人话层面的东西，不含节点 ID。"""

    prompt: str
    negative: str
    seed: int
    steps: int
    cfg: float
    width: int
    height: int
    #: 有底图就走底图复用：同一只形象换表情、换姿势，靠它把画面钉回原来的样子。
    init_image: Path | None = None
    #: 重绘强度是底图那条路上唯一的旋钮 —— 太大丢形象，太小改不动表情。
    denoise: float = 0.55


def load(path: Path | str) -> dict[str, Any]:
    """读一份 API 格式模板。返回可变 dict，注入时在副本上改。"""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkflowError(f"找不到工作流模板：{path}") from None
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{path.name} 不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or not data:
        raise WorkflowError(f"{path.name} 里没有节点")
    return data


def find_by_title(workflow: dict, title: str) -> str | None:
    """按 `_meta.title` 找节点，返回节点 ID；没有就 None。"""
    for node_id, node in workflow.items():
        if (node.get("_meta") or {}).get("title") == title:
            return str(node_id)
    return None


def missing_titles(
    workflow: dict, titles: tuple[str, ...] = REQUIRED_TITLES
) -> list[str]:
    return [title for title in titles if find_by_title(workflow, title) is None]


def _node_at(workflow: dict, title: str) -> tuple[str, dict]:
    node_id = find_by_title(workflow, title)
    if node_id is None:
        raise WorkflowError(
            f"模板里没有标题为「{title}」的节点 —— 参数注入靠标题定位，"
            "标题写错一个字就会静默失效，所以这里直接报错。"
        )
    return node_id, workflow[node_id]


def _set_input(workflow: dict, title: str, key: str, value: Any) -> None:
    node_id, node = _node_at(workflow, title)
    inputs = node.setdefault("inputs", {})
    if key not in inputs:
        raise WorkflowError(
            f"节点 {node_id}（{node.get('class_type')}）没有输入「{key}」"
            f" —— 模板和注入代码对不上了。"
        )
    inputs[key] = value


def _apply_base_image(workflow: dict, spec: GenSpec, engine_name: str | None) -> None:
    """底图那条路：把上传后的文件名与重绘强度写进模板。

    两个方向都当场报错 —— 有底图却撞上纯文生图模板、或拿底图模板出纯文生图，
    都是"整张图白出"，不如现在就拦下来。
    """
    has_base_image = find_by_title(workflow, TITLE_INIT_IMAGE) is not None
    if spec.init_image is None:
        if has_base_image:
            raise WorkflowError(
                "这份模板要一张底图（有「底图」节点），但这次没给 —— "
                "纯文生图请用 [workflows].default 指的模板。"
            )
        return
    if engine_name is None:
        raise WorkflowError(
            f"有底图（{spec.init_image}）却没给引擎侧的文件名 —— "
            "底图要先传进引擎的 input 目录，LoadImage 才认得它。"
        )
    if not has_base_image:
        raise WorkflowError(
            "这份模板没有标题为「底图」的节点，出不了底图复用 —— "
            "用 [workflows].img2img 指的底图模板。"
        )
    _set_input(workflow, TITLE_INIT_IMAGE, "image", engine_name)
    _set_input(workflow, TITLE_DENOISE, "denoise", float(spec.denoise))


def apply(workflow: dict, spec: GenSpec, engine_name: str | None = None) -> dict:
    """把业务参数注入模板副本，返回可提交的 prompt 图。原模板不动。

    engine_name 是底图在引擎 input 目录里的文件名（上传后引擎给的那个名字）。
    带底图的生成必须给它 —— LoadImage 只认自己目录里的名字。
    """
    prompt = copy.deepcopy(workflow)
    _apply_base_image(prompt, spec, engine_name)

    _set_input(prompt, TITLE_PROMPT, "text", spec.prompt)
    _set_input(prompt, TITLE_NEGATIVE, "text", spec.negative)
    _set_input(prompt, TITLE_SEED, "noise_seed", int(spec.seed))
    _set_input(prompt, TITLE_STEPS, "steps", int(spec.steps))
    _set_input(prompt, TITLE_CFG, "cfg", float(spec.cfg))
    _set_input(prompt, TITLE_SIZE, "width", int(spec.width))
    _set_input(prompt, TITLE_SIZE, "height", int(spec.height))

    # 尺寸必须全场一致：Flux2Scheduler 与 EmptyFlux2LatentImage 各带一份
    # width/height，只改一处就是尺寸打架（引擎不报错，图会变形或直接拒绝）。
    # 所以这里按输入键扫一遍，凡是同时有 width/height 的节点都跟上。
    for node_id, node in prompt.items():
        inputs = node.get("inputs") or {}
        if "width" in inputs and "height" in inputs:
            inputs["width"] = int(spec.width)
            inputs["height"] = int(spec.height)
            _ = node_id

    return prompt
