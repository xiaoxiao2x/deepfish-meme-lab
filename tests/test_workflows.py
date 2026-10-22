"""工作流模板的结构规格。

模板是 **API 格式**（不是界面格式）：每个节点一张卡片，靠节点 ID 互相引用，
引擎直接吃这个 JSON，不需要任何界面操作。

参数注入认**标题**不认节点 ID —— 重排工作流时 ID 会变，标题不会。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO_ROOT / "workflows").glob("*.json"))

# 票 #4 约定的标题集合。标题写错一个字，注入就会静默失效，所以钉死。
REQUIRED_TITLES = {"提示词", "负面词", "采样器", "种子", "步数", "输出"}

SAVING_NODES = {"SaveImage", "PreviewImage"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_at_least_one_template_exists():
    assert WORKFLOWS, "workflows/ 里一个模板都没有"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_node_is_in_api_format(path):
    for node_id, node in load(path).items():
        assert isinstance(node, dict), f"{node_id} 不是对象"
        assert "class_type" in node, f"{node_id} 缺 class_type"
        assert isinstance(node.get("inputs"), dict), f"{node_id} 缺 inputs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_links_point_at_nodes_that_exist(path):
    wf = load(path)
    for node_id, node in wf.items():
        for key, value in node["inputs"].items():
            if not (isinstance(value, list) and len(value) == 2):
                continue  # 普通值，不是连线
            assert isinstance(value[1], int), f"{node_id}.{key} 的输出下标不是整数"
            assert str(value[0]) in wf, f"{node_id}.{key} 指向不存在的节点 {value[0]}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_agreed_titles_are_present(path):
    titles = {(n.get("_meta") or {}).get("title") for n in load(path).values()}
    missing = REQUIRED_TITLES - titles
    assert not missing, f"缺标题：{sorted(missing)}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_title_stays_out_of_inputs(path):
    """标题是元数据，不该混进引擎吃的 inputs。"""
    for node_id, node in load(path).items():
        assert "title" not in node["inputs"], f"{node_id} 把标题写进 inputs 了"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_node_reaches_the_saving_node(path):
    """没有孤岛：从保存节点往回走，应该能走到每一个节点。"""
    wf = load(path)
    savers = [nid for nid, n in wf.items() if n["class_type"] in SAVING_NODES]
    assert savers, "没有保存节点"

    seen: set[str] = set()
    queue = list(savers)
    while queue:
        node_id = queue.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for value in wf[node_id]["inputs"].values():
            if isinstance(value, list) and len(value) == 2:
                queue.append(str(value[0]))

    assert seen == set(wf), f"够不着的节点：{sorted(set(wf) - seen)}"
