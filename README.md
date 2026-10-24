# 鱼片加工厂 · deepfish-meme-lab

一个只为自己用的**本地工作台**：生成 DeepSeek 鲸鱼娘与蓝色大肥鱼的梗图、表情包。

把「想要一张鲸鱼娘表情包」到「手上有一张能直接用的图」之间的所有手工步骤，收成**一条命令**和**一个页面**。

---

## 状态

**实现中** —— 设计与路线已定稿；第一阶段（M1–M4）按 [票](https://github.com/xiaoxiao2x/deepfish-meme-lab/issues) 推进。

M1 环境补齐、M2 出图管线、M3 的去背与加字、M4 的两本账都已跑通：`python -m lab make --prompt "..." --count 4` 会真的调用本地 ComfyUI 出图，落到 `output/<日期>/fish-001.orig.png`，终端打印每张的路径与 seed；带 `--init-image` 时换成另一张模板做底图复用，把形象钉在原来那张图上；`python -m lab sticker <图片>` 把一张图去背成透明底贴纸（沿用原图序号，落在原图旁边）；`python -m lab caption <贴纸> --text "你好呀"` 再给它印一句台词，出自带白描边的带字成图。每次落盘都顺手在当天记一笔（`output/<日期>/registry.local.json`，含废图，不进 git）；挑中的那张用 `python -m lab keep <图片>` 连种子和参数一起搬进配方库 `data/registry.json`（进 git），`python -m lab gallery` 看库里有什么。`python -m lab reproduce fish-001` 拿库里那条配方再跑一次，当场比出「逐像素一致 / 不一样 / 没得比」。M3 还剩规格导出。

## 跑起来

```bash
python3 -m lab doctor        # 体检：引擎、权重、节点、模板契约、素材、输出目录
python3 -m lab make --prompt "chibi blue whale girl, sticker style" --count 4
                            # 出一批图，落到 output/<日期>/，打印路径与 seed
python3 -m lab make --prompt "同一只鲸鱼娘，换个表情" \
    --init-image output/<日期>/fish-001.orig.png --denoise 0.45
                            # 底图复用：拿一张旧图当底，把形象钉住（0.45 是默认，可省）
python3 -m lab sticker output/<日期>/fish-001.orig.png
                            # 去背：出一张透明底贴纸，落在原图旁边（同名同序号）
                            # 别的图也行；用哪只去背模型写在模板的 model 字段里
python3 -m lab caption output/<日期>/fish-001.sticker.png --text "你好呀"
                            # 加字：带白描边的带字成图，落在贴纸旁边（同名同序号）
                            # 台词长了会自动折行；字体与字号在 config 的 [postprocess] 里
python3 -m lab keep output/<日期>/fish-001.captioned.png
                            # 保留：挑中的那张从当天的账里连参数一起搬进配方库（进 git 的那本）
                            # 原图 / 贴纸 / 带字成图都能留；账里没有来路就只收图、参数留空
python3 -m lab gallery      # 配方库里留了什么：号 / 阶段 / 模型 / 种子 / 时间
python3 -m lab reproduce fish-001
                            # 复现：照库里那条配方重跑一张（落到今天那个目录、进当天的账）
                            # 与原图逐像素比：同引擎版本下应当一致；不一致会说差在哪儿
python3 -m lab assets scan   # 扫描素材、写索引、报重复（只读，不动原图）
python3 -m lab assets dupes  # 按索引列出重复组
python3 -m lab stub          # 起个假引擎：没显卡也能把这段走通（--rgba 时回的图带透明通道）
```

三方依赖只有一个 **Pillow**（读图算哈希、回读校验去背成品的 alpha）；`lab doctor` 连它都不碰，纯标准库。
去背的模型推理在引擎里做（ComfyUI-RMBG 的节点），工作台只负责递图与收图 —— 首次用到某只模型时引擎会自己下载权重。
系统的 `python3` 直接跑（`tomllib` 要 3.11+）。

跑测试需要 pytest 与 Pillow；静态检查用 ruff 与 mypy：

```bash
python3 -m venv .venv
.venv/bin/pip install pytest Pillow ruff mypy
```

改完代码跑门禁 —— 四条都过才算过：

```bash
./scripts/check.sh          # pytest + ruff + mypy + docs
./scripts/check.sh ruff     # 只跑其中一条
```

`scripts/git-hooks/pre-commit` 会在提交前自动调一遍，每个克隆装一次：
`git config core.hooksPath scripts/git-hooks`；某次确实想跳过：`SKIP_CHECKS=1 git commit ...`。
检查用哪些规则写在 `pyproject.toml` 的 `[tool.ruff]` / `[tool.mypy]` 里，不靠工具默认值
（本次验证的版本：ruff 0.17.1、mypy 2.4.0、Python 3.11+）。
`docs` 那一条（`scripts/check_docs.py`）比四件事：`docs/design.md` 写的标题集合与
`lab.workflows` 的常量是否一致、§6.2 的 schema 键与 `Entry.as_json()` 是否一致、
`lab/` 的每个文件在不在 §4 的架构树上、源码与模板里有没有用 `GLOSSARY.md` 不收的词。确实该那么写时
在那一行加 `glossary-ok`，理由写在旁边。

出图要引擎先跑着 —— 工作台不负责启停（[ADR-0004](docs/adr/0004-no-comfyui-lifecycle.md)）：

```bash
./scripts/comfy.sh start    # 起 ComfyUI，等到 8188 就绪才返回
./scripts/comfy.sh status   # 查通不通
./scripts/comfy.sh log      # 看日志
./scripts/comfy.sh stop
```

## 一份临时仓库

验边界、试新配置、跑一次不想留在真仓库里的实验时，别在真仓库上动手：`--repo-root 目录`（或环境变量 `LAB_REPO_ROOT`）把工作台整个钉在另一个目录上 —— 配置、`output/`、`data/` 都按它算，真仓库一个字节不动。

最少两样就能出片：

```bash
SCRATCH=/tmp/lab-scratch
mkdir -p "$SCRATCH/config"
cp config/default.toml "$SCRATCH/config/"
cp -r workflows "$SCRATCH/workflows"
```

`data/` 与 `output/` 不用预先建 —— 第一次 `lab keep` 会写出 `registry.json`，出片时按当天日期建目录。

缺什么当场说，不崩在深处：

```text
配置读不出来：找不到 default.toml（应在 <临时仓库>/config/default.toml）
工作流模板有问题：找不到工作流模板：<临时仓库>/workflows/fish-txt2img.json
```

先看一眼：`python3 -m lab doctor --repo-root "$SCRATCH"` —— 配置里没指 `comfy.root` 与 `asset_roots` 时那两项报 WARN，其余照查（引擎连通性、模板契约、输出目录）。

要和真仓库的引擎同时跑（不抢 8188），在临时仓库里覆盖端口就行 —— `config/local.toml` 深合并进 `default.toml`，只盖点名的键：

```bash
printf '[comfy]\nport = 8288\n' > "$SCRATCH/config/local.toml"
```

没有显卡、或只想把链路走通：`python3 -m lab stub --repo-root "$SCRATCH"` 起个假引擎（它听的也是这个仓库配的 `comfy.port`）：

```bash
python3 -m lab stub --repo-root "$SCRATCH" &
python3 -m lab make -p "chibi blue whale girl, sticker style" -n 2 --repo-root "$SCRATCH"
# → $SCRATCH/output/<日期>/fish-001.orig.png、fish-002.orig.png + registry.local.json
```

假引擎回的图只有 64×64、默认不透明：要走 `lab sticker` 就加 `--rgba`（不透明的图证明不了去背，那一步会当场拒收）；`lab caption` 在 64×64 上会如实报「画布太窄」——它验的是链路通不通，不是出片好看不好看。

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
