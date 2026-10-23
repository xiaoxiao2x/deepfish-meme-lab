"""模板与引擎节点契约的对账。

模板是 API 格式 JSON：里面的每个 `class_type`、每个输入键、每条连线的下标，
最终都得由引擎认账（`GET /object_info` 会把它的节点清单交出来）。手工照着节点
源码再抄一遍清单，等于给自己造第二份真相源 —— 本模块把它做成一次只读对账：

    class_type 不在这台引擎上   装的节点包不对，或者节点升级改了名
    输入键不在节点的输入表里     拼错一个字母，提交时才炸
    连线的上游输出不存在         下标越界
    连线的类型对不上             SaveImage 接 MASK，引擎回 400（#9 实测）
    下拉值不在候选里             model / sampler_name 这类名字打错
    标量类型不对                 INT 的位置写了个字符串

**不查**运行时注入的值（种子、步数、尺寸）：模板里写的是默认值，真正提交的是注入
之后的那份图，要查那种越界得在提交前查，那是另一件事。

纯函数进出，不碰网络 —— object_info 由调用方（lab.doctor）去取。
"""

from __future__ import annotations

from typing import Any

__all__ = ["problems"]

#: 引擎里表示「什么类型都收」的几种写法。
WILDCARD_TYPES = frozenset({"*", "ANY", "COMFY_ANY"})

#: 候选由运行时决定的输入：这些 opts 一出现就说明下拉里的值是引擎现场扫出来的
#: （比如 LoadImage 的图片列表），模板里写什么都可能是对的。
_DYNAMIC_FLAGS = ("image_upload", "file_upload", "upload")

#: 引擎里那些「只能由上游节点的输出喂进来」的类型。它们出现在字面值的位置上
#: 一定是错的（写死一张图、一个 latent 都不可能是模板的本意）。
_OBJECT_TYPES = frozenset(
    {
        "IMAGE",
        "MASK",
        "LATENT",
        "MODEL",
        "CLIP",
        "VAE",
        "CONDITIONING",
        "SIGMAS",
        "NOISE",
        "GUIDER",
        "SAMPLER",
    }
)


def problems(
    workflow: dict[str, Any], object_info: dict[str, Any], *, name: str = ""
) -> list[str]:
    """把一份模板对着引擎的节点清单过一遍，返回人话问题清单（空 = 对得上）。

    name 只是给人看的模板名（会缀在每条问题前面），不参与判断。
    """
    where = f"{name} " if name else ""
    found: list[str] = []

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            found.append(f"{where}节点 {node_id} 不是个节点对象")
            continue

        class_type = str(node.get("class_type") or "")
        label = (
            f"{where}节点 {node_id}"
            f"（{class_type or '没写 class_type'}{_title_suffix(node)}）"
        )
        spec = object_info.get(class_type)
        if not isinstance(spec, dict):
            found.append(
                f"{label}：这台引擎上没这个节点类 —— 节点包没装齐，或者节点升级改了名"
            )
            continue

        table = _input_table(spec)
        for key, value in (node.get("inputs") or {}).items():
            declared = table.get(key)
            if declared is None:
                known = "、".join(sorted(table)) or "（它一个输入都没有）"
                found.append(
                    f"{label}：没有输入「{key}」—— 名字拼错了，或者节点升级换了名"
                    f"（这台引擎认这些：{known}）"
                )
                continue

            type_spec, options, dynamic = _declared(declared)
            link = _link_target(value, workflow)
            if link is not None:
                found.extend(
                    _link_problems(label, key, link, workflow, object_info, type_spec)
                )
                continue

            if options and not dynamic and _missing_from(value, options):
                found.append(
                    f"{label}：输入「{key}」是 {value!r}，不在引擎给的候选里"
                    f"（{_preview(options)}）"
                )
                continue
            if not _value_fits(type_spec, value):
                hint = "—— 这种只能接上游节点的输出" if type_spec in _OBJECT_TYPES else ""
                found.append(
                    f"{label}：输入「{key}」要 {type_spec}，模板里给的是 "
                    f"{type(value).__name__}（{value!r}）{hint}"
                )

    return found


# ── 零件 ──────────────────────────────────────────────────────────────


def _title_suffix(node: dict[str, Any]) -> str:
    title = (node.get("_meta") or {}).get("title")
    return f"·{title}" if title else ""


def _input_table(spec: dict[str, Any]) -> dict[str, Any]:
    """节点的输入表：required 与 optional 合起来 —— 对模板来说两种都能写。"""
    declared = spec.get("input") or {}
    table: dict[str, Any] = {}
    for section in ("required", "optional"):
        table.update(declared.get(section) or {})
    return table


def _declared(value: Any) -> tuple[str, list[Any] | None, bool]:
    """一个输入的声明 →（类型名, 候选, 候选是不是运行时才有）。

    /object_info 里候选有两种形状：老的是 [["a", "b"], {...}]，新的是
    ["COMBO", {"options": [...]}]。两种都收成「COMBO + 候选」。
    """
    if not isinstance(value, list) or not value:
        return "", None, False
    head = value[0]
    opts = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
    dynamic = any(flag in opts for flag in _DYNAMIC_FLAGS)
    if head == "COMBO":
        return "COMBO", list(opts.get("options") or []), dynamic
    if isinstance(head, list):
        return "COMBO", list(head), dynamic
    return str(head), None, dynamic


def _link_target(value: Any, workflow: dict[str, Any]) -> tuple[str, int] | None:
    """值是「连到某个节点第几个输出」吗？不是就是字面值，返回 None。"""
    if not isinstance(value, list) or len(value) != 2:
        return None
    target, index = value
    if (
        isinstance(target, str)
        and target in workflow
        and isinstance(index, int)
        and not isinstance(index, bool)
    ):
        return target, index
    return None


def _link_problems(
    label: str,
    key: str,
    link: tuple[str, int],
    workflow: dict[str, Any],
    object_info: dict[str, Any],
    type_spec: str,
) -> list[str]:
    target, index = link
    upstream = workflow.get(target)
    up_class = str(upstream.get("class_type") or "") if isinstance(upstream, dict) else ""
    up_spec = object_info.get(up_class)
    outputs = (
        [str(t) for t in (up_spec.get("output") or [])] if isinstance(up_spec, dict) else []
    )
    if index < 0 or index >= len(outputs):
        return [
            f"{label}：输入「{key}」接的是节点 {target}（{up_class or '？'}）"
            f"第 {index} 个输出，可它只有 {len(outputs)} 个输出"
        ]
    if not _accepts(type_spec, outputs[index]):
        return [
            f"{label}：输入「{key}」要 {type_spec}，接的却是节点 {target}（{up_class}）"
            f"第 {index} 个输出 —— 那是 {outputs[index]}"
        ]
    return []


def _accepts(type_spec: str, upstream: str) -> bool:
    """这个输入收得下上游那个类型吗？"""
    if not type_spec or type_spec == "COMBO":
        return True
    if type_spec in WILDCARD_TYPES or upstream in WILDCARD_TYPES:
        return True
    return type_spec == upstream


def _value_fits(type_spec: str, value: Any) -> bool:
    """字面值的类型对得上吗？

    两头都卡：认得的标量严格比类型；「只能接连线」的对象类型一出现就是错的。
    认不出来的类型（COLORCODE 之类）放过 —— 宁可漏，不可冤。
    """
    if type_spec in _OBJECT_TYPES:
        return False
    if type_spec == "INT":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_spec == "FLOAT":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_spec == "BOOLEAN":
        return isinstance(value, bool)
    if type_spec == "STRING":
        return isinstance(value, str)
    return True


def _missing_from(value: Any, options: list[Any]) -> bool:
    """值（或多个值，多选的下拉就是列表）里有不在候选里的吗？

    比较时类型也要一样：True 和 1 在 Python 里相等，但那不是「候选里有」。
    """
    values = value if isinstance(value, list) else [value]
    return any(
        not any(type(candidate) is type(item) and candidate == item for candidate in options)
        for item in values
    )


def _preview(options: list[Any], limit: int = 6) -> str:
    shown = "、".join(str(option) for option in options[:limit])
    return shown + ("…" if len(options) > limit else "")
