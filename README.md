# 鱼片加工厂 · deepfish-meme-lab

一个只为自己用的**本地工作台**：生成 DeepSeek 鲸鱼娘与蓝色大肥鱼的梗图、表情包。

把「想要一张鲸鱼娘表情包」到「手上有一张能直接用的图」之间的所有手工步骤，收成**一条命令**和**一个页面**。

---

## 状态

**实现中** —— 设计与路线已定稿；第一阶段（M1–M4）按 [票](https://github.com/xiaoxiao2x/deepfish-meme-lab/issues) 推进。

M1 环境补齐与 M2 出图管线都已跑通：`python -m lab make --prompt "..." --count 4` 会真的调用本地 ComfyUI 出图，落到 `output/<日期>/fish-001.orig.png`，终端打印每张的路径与 seed。下一步是 M3 后处理（去背 / 加字 / 导出）。

## 跑起来

```bash
python3 -m lab doctor        # 体检：引擎、权重、节点、素材、输出目录
python3 -m lab make --prompt "chibi blue whale girl, sticker style" --count 4
                            # 出一批图，落到 output/<日期>/，打印路径与 seed
python3 -m lab assets scan   # 扫描素材、写索引、报重复（只读，不动原图）
python3 -m lab assets dupes  # 按索引列出重复组
python3 -m lab stub          # 起个假引擎：没显卡也能把这段走通
```

三方依赖只有一个 **Pillow**（读图算哈希用）；`lab doctor` 连它都不碰，纯标准库。
系统的 `python3` 直接跑（`tomllib` 要 3.11+）。

跑测试需要 pytest 与 Pillow（装在 `.venv` 里）：

```bash
python3 -m venv .venv
.venv/bin/pip install pytest Pillow        # 静态检查可选：ruff mypy
.venv/bin/python -m pytest
```

## 文档

| 文件 | 回答的问题 |
|---|---|
| [docs/design.md](docs/design.md) | 这东西**是什么**、**不做什么**、架构、技术选型、数据格式、GUI 大致设计 |
| [docs/roadmap.md](docs/roadmap.md) | **先做什么**、**做到什么程度算成**、已知的坑 |
| [GLOSSARY.md](GLOSSARY.md) | 每个词在这个项目里到底指什么 |
| [docs/adr/](docs/adr/) | 为什么做了这些不那么显然的决定 |

## 它不做什么

不做网站、不做素材站、不训 LoRA、不做多用户、不接在线生图 API、不管上架发布——完整清单见设计文档 §3.2「非目标」。

## 依赖

- 本地 **ComfyUI**（RTX 5070 Ti 16 GB；基座 FLUX.2 Klein 4B）
- Python + Pillow（图像处理）、ffmpeg（动图，后续）

## 素材与输出

素材池当前 **415 张**（大肥鱼那批 217 + 素材库的 198），**不进本仓库**——它们是只读引用，实际路径写在 `config/local.toml`。

仓库里只保留真正值钱的两样：`workflows/`（ComfyUI 工作流模板）与 `data/registry.json`（可复现的生成配方）。

## 许可

非商业二创立场（模型本身是 Apache-2.0 可商用，非商业是本项目的主动选择）。详见设计文档 §11。
