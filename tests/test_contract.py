"""模板与引擎节点清单的对账（lab.contract）。

对账的价值全在「错了能不能说清楚」上，所以这里逐类造一个错，看它报不报、报得对不对。

节点清单用 lab.stub 里那份**真机缩样**（照 ComfyUI 0.42 抄的）：对账本来就是拿
一份外部真相源比模板，测试不该为了比这一下就去连真引擎。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.contract import problems
from lab.stub import STUB_OBJECT_INFO

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO_ROOT / "workflows").glob("*.json"))

# 一个只有两个输出的上游：验连线的类型与下标都靠它
UPSTREAM = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sole(class_type: str, inputs: dict, upstream: dict | None = UPSTREAM) -> list[str]:
    """一份最小模板：1 号是上游，2 号是要检查的那个节点。"""
    workflow: dict = {}
    if upstream is not None:
        workflow["1"] = upstream
    workflow["2"] = {"class_type": class_type, "inputs": inputs}
    return problems(workflow, STUB_OBJECT_INFO)


# ── 真模板 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_every_template_in_the_repo_matches_the_engine(path):
    """三份模板都得对得上 —— 对不上就是模板写错了（节点包没装齐、名字抄错、下标写歪）。"""
    assert problems(load(path), STUB_OBJECT_INFO, name=path.name) == []


# ── 逐类错 ────────────────────────────────────────────────────────────


def test_a_node_class_the_engine_does_not_have():
    """节点包没装、或者节点升级改了名 —— #9 里那两个坑（mask_blur KeyError、MASK 400）的表亲。"""
    found = sole("BiRefNetRMBGXX", {"image": ["1", 0]})
    assert len(found) == 1
    assert "BiRefNetRMBGXX" in found[0]
    assert "没这个节点类" in found[0]


def test_an_input_key_that_was_typoed():
    """拼错一个字母：引擎那边是 KeyError / 校验失败，这边要当场说出来，还得报它认哪些键。"""
    found = sole("BiRefNetRMBG", {"image": ["1", 0], "mode": "Lucida"})
    assert len(found) == 1
    assert "没有输入「mode」" in found[0]
    assert "mask_blur" in found[0]  # 引擎认得的键清单里得有它


def test_a_link_to_an_output_that_does_not_exist():
    """连线下标越界：LoadImage 只有两个输出，写个 2 就是在赌引擎不检查。"""
    found = sole("SaveImage", {"images": ["1", 2], "filename_prefix": "fish/x"})
    assert len(found) == 1
    assert "第 2 个输出" in found[0]
    assert "只有 2 个输出" in found[0]


def test_a_link_with_the_wrong_type():
    """接错输出：SaveImage 要 IMAGE，接上 LoadImage 的 MASK 那一路 —— 引擎回 400 的那件事。"""
    found = sole("SaveImage", {"images": ["1", 1], "filename_prefix": "fish/x"})
    assert len(found) == 1
    assert "要 IMAGE" in found[0]
    assert "那是 MASK" in found[0]


def test_a_literal_that_is_not_one_of_the_choices():
    """下拉里没有的名字（模型名、采样器名）：引擎那边是 value_not_in_list。"""
    found = sole("BiRefNetRMBG", {"image": ["1", 0], "model": "BiRefNet-nope"})
    assert len(found) == 1
    assert "不在引擎给的候选里" in found[0]
    assert "Lucida" in found[0]


def test_a_literal_with_the_wrong_scalar_type():
    """INT 的位置写了个字符串：模板是手写的，这种错一眼看不出来。"""
    found = sole("BiRefNetRMBG", {"image": ["1", 0], "mask_blur": "三"})
    assert len(found) == 1
    assert "要 INT" in found[0]
    assert "str" in found[0]


def test_an_image_input_given_as_a_literal():
    """得接连线的地方给了字面值 —— IMAGE 这种值只可能来自上游节点的输出。"""
    found = sole("SaveImage", {"images": "x", "filename_prefix": "fish/x"})
    assert len(found) == 1
    assert "只能接上游节点的输出" in found[0]


def test_a_node_that_is_not_a_node():
    """模板被手改坏了：节点位置放了个字符串。"""
    found = problems({"1": "SaveImage"}, STUB_OBJECT_INFO)
    assert len(found) == 1
    assert "不是个节点对象" in found[0]


# ── 不该报的 ──────────────────────────────────────────────────────────


def test_choices_the_engine_scans_at_runtime_are_let_through():
    """LoadImage 的候选是引擎现场扫的：模板里写哪张都可能是对的，不能报。"""
    assert problems({"1": {"class_type": "LoadImage", "inputs": {"image": "我随便写的.png"}}}, STUB_OBJECT_INFO) == []


def test_the_new_combo_shape_is_read_too():
    """真机有一批节点把候选藏在 opts 里（[\"COMBO\", {\"options\": [...]}]），老写法也一样认。"""
    assert sole("KSamplerSelect", {"sampler_name": "euler"}) == []
    found = sole("KSamplerSelect", {"sampler_name": "euler2"})
    assert len(found) == 1
    assert "不在引擎给的候选里" in found[0]


def test_a_multi_select_value_is_checked_one_by_one():
    """多选下拉的值是个列表：逐个查，有一个不在候选里就报。"""
    assert sole("KSamplerSelect", {"sampler_name": ["euler", "heun"]}) == []
    found = sole("KSamplerSelect", {"sampler_name": ["euler", "没有的"]})
    assert len(found) == 1
    assert "没有的" in found[0]


def test_a_wildcard_input_takes_anything():
    """声明成 * / ANY 的输入什么都收 —— 这是引擎自己的说法，别替它做主。"""
    object_info = {
        "Wild": {"input": {"required": {"any": ["*"]}}, "output": ["ANY"]},
    }
    assert problems({"1": {"class_type": "Wild", "inputs": {"any": {"乱七八糟": 1}}}}, object_info) == []


def test_the_template_name_is_put_in_front():
    """多份模板一起对账时，问题得能指回是哪一份。"""
    found = sole("BiRefNetRMBG", {"image": ["1", 0], "model": "没有的"})
    assert problems(
        {"2": {"class_type": "BiRefNetRMBG", "inputs": {"image": ["1", 0], "model": "没有的"}},
         "1": UPSTREAM},
        STUB_OBJECT_INFO,
        name="fish-x.json",
    )[0].startswith("fish-x.json 节点 2")
    assert found  # 同一份模板不带名字时照样报
