"""工作流模板的结构规格，以及参数注入的契约。

模板是 **API 格式**（不是界面格式）：每个节点一张卡片，靠节点 ID 互相引用，
引擎直接吃这个 JSON，不需要任何界面操作。

参数注入认**标题**不认节点 ID —— 重排工作流时 ID 会变，标题不会。

后半段接着测注入本身（apply 往哪个标题写了什么值）：那是同一件事的另一半 ——
标题对了但注入落错节点，一样是白出一张图。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab.workflows import REQUIRED_TITLES as INJECTED_TITLES
from lab.workflows import TITLE_OUTPUT, TITLE_SAMPLER, find_by_title

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((REPO_ROOT / "workflows").glob("*.json"))

#: 生成模板：走完整生成流程的那两份，那一串采样标题归它们管（票 #4 / #8）。
GENERATION = [
    REPO_ROOT / "workflows" / name
    for name in ("fish-txt2img.json", "fish-img2img.json")
]

#: 后处理模板：去背，没有采样链也没有提示词（票 #9）。
STICKER = REPO_ROOT / "workflows" / "fish-sticker.json"


def test_every_template_is_classified():
    """新加的模板得先在生成/后处理里归好类 —— 否则它会被静默漏测。"""
    classified = {*GENERATION, STICKER}
    unclassified = sorted(p.name for p in set(WORKFLOWS) - classified)
    assert not unclassified, f"没归类的模板：{unclassified}"

#: 模板结构上必须有的标题 = 注入要的那几个（唯一定义在 lab.workflows，票 #4 / #7）
#: +「采样器」（模板的选择，不注入）+「输出」（只要存在）。
#: 以前这里是第三份手抄，和 design.md、lab.workflows 三处各写一遍 —— 对不上的时候
#: 没人知道该信哪份。现在只有一处定义，文档那份由 scripts/check_docs.py 对着比。
REQUIRED_TITLES = {*INJECTED_TITLES, TITLE_SAMPLER, TITLE_OUTPUT}

SAVING_NODES = {"SaveImage", "PreviewImage"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _titles(wf: dict) -> set[str]:
    """模板里出现的所有 _meta.title —— "有没有那个标题"全靠它判断。

    只收字符串：缺 _meta、或 _meta 里没 title 的节点不该在集合里留一个 None ——
    差集（REQUIRED_TITLES - _titles(...)）里混进 None，报出来的「缺标题」就成了
    一句话看不懂的东西。
    """
    found = {(n.get("_meta") or {}).get("title") for n in wf.values()}
    return {title for title in found if isinstance(title, str)}


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


@pytest.mark.parametrize("path", GENERATION, ids=lambda p: p.name)
def test_agreed_titles_are_present(path):
    missing = REQUIRED_TITLES - _titles(load(path))
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


# ---------- 底图复用模板（票 #8） ----------

IMG2IMG = REPO_ROOT / "workflows" / "fish-img2img.json"
TXT2IMG = REPO_ROOT / "workflows" / "fish-txt2img.json"


def _only(wf: dict, class_type: str) -> tuple[str, dict]:
    hits = [(nid, n) for nid, n in wf.items() if n["class_type"] == class_type]
    assert len(hits) == 1, f"{class_type} 出现 {len(hits)} 次，模板结构变了"
    return hits[0]


def test_the_two_templates_are_kept_apart():
    """有没有底图是两条不同的路，别把两者混进一个模板里猜。"""
    assert {"底图", "重绘强度"} <= _titles(load(IMG2IMG))
    assert "底图" not in _titles(load(TXT2IMG))
    assert "重绘强度" not in _titles(load(TXT2IMG))


def test_img2img_encodes_the_base_image_instead_of_empty_noise():
    wf = load(IMG2IMG)
    _, sampler = _only(wf, "SamplerCustomAdvanced")
    encoder = wf[sampler["inputs"]["latent_image"][0]]
    assert encoder["class_type"] == "VAEEncode"


def test_img2img_scales_the_base_image_before_encoding_it():
    """底图先缩到画布再编码 —— 否则 latent 与调度器的 width/height 会打架。"""
    wf = load(IMG2IMG)
    _, sampler = _only(wf, "SamplerCustomAdvanced")
    encoder = wf[sampler["inputs"]["latent_image"][0]]
    scale = wf[encoder["inputs"]["pixels"][0]]
    assert scale["class_type"] == "ImageScale"
    assert (scale.get("_meta") or {}).get("title") == "尺寸"
    assert scale["inputs"]["width"] and scale["inputs"]["height"]
    assert wf[scale["inputs"]["image"][0]]["class_type"] == "LoadImage"


def test_img2img_puts_denoise_between_the_scheduler_and_the_sampler():
    """重绘强度是唯一旋钮：截 Flux2 的 sigma 曲线，而不是换一套调度器。"""
    wf = load(IMG2IMG)
    split_id = find_by_title(wf, "重绘强度")
    assert split_id is not None
    split = wf[split_id]
    assert split["class_type"] == "SplitSigmasDenoise"
    assert wf[split["inputs"]["sigmas"][0]]["class_type"] == "Flux2Scheduler"

    _, sampler = _only(wf, "SamplerCustomAdvanced")
    # 下标 1 是 low_sigmas：从"只走完 denoise 那一段"开始采样
    assert sampler["inputs"]["sigmas"] == [split_id, 1]


# ---------- 注入契约 ----------


def _spec(**overrides):
    from lab.workflows import GenSpec

    base = {
        "prompt": "x",
        "negative": "",
        "seed": 1,
        "steps": 20,
        "cfg": 5.0,
        "width": 1024,
        "height": 1024,
    }
    return GenSpec(**{**base, **overrides})


def test_a_txt2img_template_refuses_a_base_image():
    """给了底图却拿文生图模板 —— 报错，别静默出一张跟底图无关的图。"""
    from lab.workflows import WorkflowError, apply

    with pytest.raises(WorkflowError) as caught:
        apply(load(TXT2IMG), _spec(init_image=Path("base.png")), engine_name="lab-init-a.png")

    assert "底图" in str(caught.value)


def test_an_img2img_template_demands_a_base_image():
    from lab.workflows import WorkflowError, apply

    with pytest.raises(WorkflowError) as caught:
        apply(load(IMG2IMG), _spec())

    assert "底图" in str(caught.value)


def test_apply_injects_the_engine_name_and_the_denoise():
    from lab.workflows import apply, find_by_title

    prompt = apply(
        load(IMG2IMG),
        _spec(init_image=Path("base.png"), denoise=0.42),
        engine_name="lab-init-abc.png",
    )

    assert prompt[find_by_title(prompt, "底图")]["inputs"]["image"] == "lab-init-abc.png"
    assert prompt[find_by_title(prompt, "重绘强度")]["inputs"]["denoise"] == 0.42
    assert prompt[find_by_title(prompt, "尺寸")]["inputs"]["width"] == 1024


def test_apply_keeps_the_text_only_path_unchanged():
    from lab.workflows import apply

    prompt = apply(load(TXT2IMG), _spec())

    assert "LoadImage" not in {n["class_type"] for n in prompt.values()}


def test_apply_does_not_touch_the_template_it_was_given():
    from lab.workflows import apply, find_by_title

    template = load(IMG2IMG)
    before = template[find_by_title(template, "底图")]["inputs"]["image"]

    apply(
        template,
        _spec(init_image=Path("base.png")),
        engine_name="lab-init-abc.png",
    )

    assert template[find_by_title(template, "底图")]["inputs"]["image"] == before


# ---------- 去背模板（票 #9） ----------


def _node_by_title(prompt: dict, title: str) -> dict:
    node_id = find_by_title(prompt, title)
    assert node_id is not None, f"prompt 里没有标题「{title}」"
    return prompt[node_id]


def test_the_sticker_template_carries_the_agreed_titles():
    from lab.workflows import STICKER_TITLES

    assert set(STICKER_TITLES) <= _titles(load(STICKER))


def test_the_sticker_template_saves_the_alpha_output():
    """三路输出里接哪一路是决定性的：下标 0 才是带透明区的 IMAGE。"""
    wf = load(STICKER)
    remover_id, remover = _only(wf, "BiRefNetRMBG")
    assert remover["inputs"]["background"] == "Alpha"
    assert remover["inputs"]["model"] == "Lucida"  # 首选：专攻插画/贴纸/线稿
    assert wf[remover["inputs"]["image"][0]]["class_type"] == "LoadImage"

    _, saver = _only(wf, "SaveImage")
    assert saver["inputs"]["images"] == [remover_id, 0]


def test_the_sticker_template_spells_out_every_input_the_node_reads_blindly():
    """`process_image` 用 `params["mask_blur"]` 这样直接取参 —— API 提交少写一个键
    就是 KeyError（#9 实测栽在 `'mask_blur'` 上）。界面用不会踩：界面会把 widget
    的值都写进 prompt。"""
    _, remover = _only(load(STICKER), "BiRefNetRMBG")

    for key in (
        "sensitivity",
        "mask_blur",
        "mask_offset",
        "invert_output",
        "refine_foreground",
        "unload_model",
        "background",
        "background_color",
    ):
        assert key in remover["inputs"], f"「去背」节点少了 {key}，引擎会 KeyError"


def test_apply_sticker_injects_the_uploaded_name():
    from lab.workflows import apply_sticker

    prompt = apply_sticker(load(STICKER), engine_name="lab-init-abc.png")

    assert _node_by_title(prompt, "图片")["inputs"]["image"] == "lab-init-abc.png"
    assert _node_by_title(prompt, "去背")["inputs"]["model"] == "Lucida"


def test_sticker_model_reads_the_template():
    from lab.workflows import sticker_model

    assert sticker_model(load(STICKER)) == "Lucida"


def test_sticker_model_is_quiet_when_there_is_no_removal_node():
    """回执不该因为缺节点就炸 —— 那种模板 apply_sticker 那一关已经先报错了。"""
    from lab.workflows import sticker_model

    assert sticker_model({"1": {"class_type": "LoadImage", "inputs": {}}}) == ""


def test_apply_sticker_leaves_the_template_alone():
    from lab.workflows import apply_sticker

    template = load(STICKER)
    before = _node_by_title(template, "图片")["inputs"]["image"]

    apply_sticker(template, engine_name="lab-init-abc.png")

    assert _node_by_title(template, "图片")["inputs"]["image"] == before


def test_a_template_without_the_removal_node_is_refused():
    """标题写错一个字就当场报错，别拿一张没去过背的图冒充贴纸。"""
    from lab.workflows import WorkflowError, apply_sticker

    broken = {
        nid: node
        for nid, node in load(STICKER).items()
        if node["class_type"] != "BiRefNetRMBG"
    }

    with pytest.raises(WorkflowError) as caught:
        apply_sticker(broken, engine_name="x.png")

    assert "去背" in str(caught.value)
