# 鱼片加工厂 · 设计文档

> 项目代号 `deepfish-meme-lab` ｜ 中文名：**鱼片加工厂** ｜ 状态：**共识文档（尚未写实现代码）**
> 生成日期：2026-10-22 ｜ 本文件回答"这东西**是什么**、**不做什么**"；阶段与验收见 [roadmap.md](./roadmap.md)

---

## 0. 一句话

把「想要一张 DeepSeek 鲸鱼娘表情包」到「手上有一张能直接用的图」之间的所有手工步骤，收成**一条命令**和**一个页面**。

---

## 1. 为什么不直接用 ComfyUI

ComfyUI 能画，而且画得比工作台好——它本来就是这个项目的地基，不是要替代的对象。但直接用它有四个具体的难受点，工作台的存在理由就是这四个：

| 难受点 | 具体表现 | 工作台怎么解 |
|---|---|---|
| **要会 ComfyUI** | 30+ 节点的图里找那个 prompt 框；改参数、点队列、等图、换 seed 再来一轮 | 一条命令 / 一个页面，参数是业务参数（台词、形象、张数），不是节点参数 |
| **形象会漂** | 今天这只鲸鱼娘和昨天那只不是同一只 | img2img 复用底图 + 参考图引导，锁住"同一只" |
| **出图不成活** | ComfyUI 给的是"一张 PNG"，表情包要的是"透明底、能贴上去的图" | 内置去背、加字、规格导出 |
| **不可复现** | 上周那张很满意的图，今天找不回参数 | 每次产出都写登记册（prompt / seed / 模型 / 来源图） |

> 一句话概括分工：**ComfyUI 负责"画得像"，工作台负责"要画什么、画成什么样、画完怎么用"。**

---

## 2. 使用者与规模

**一个人。** 作者本人在自家 PC 上使用。

结构上给「少数朋友」留一道门，但**不建设任何多用户能力**——没有账号、没有配额、没有并发隔离、没有权限模型。

"留门"的具体含义只有一条：**本地 Web 服务换个监听地址（`--host 0.0.0.0`）就能让局域网内的朋友访问**，不做鉴权，靠内网隔离。这条不需要专门设计，它是本地 Web UI 形态的副产品。

**公开共创是明确的非目标。**

---

## 3. 边界

### 3.1 做

| 能力 | 说明 |
|---|---|
| **出图** | 调本地 ComfyUI，按模板工作流出图，一次多张 |
| **一致性** | 底图复用（img2img）+ 参考图引导，维持"同一只鲸鱼娘" |
| **去背** | 输出透明底贴纸 PNG（RGBA） |
| **加字** | 台词排版成图 |
| **登记** | 记录 prompt / seed / 模型 / 来源，可复现 |
| **挑图** | 批量结果浏览、重跑、导出 |

### 3.2 不做（非目标）

非目标与目标同等重要——它们是这次共识里最贵的产出，因为它们决定后面**不会**把时间花在哪里。

| 不做的事 | 为什么 |
|---|---|
| 网站 / 备案子域 / R2 / 云服务器 | "先做成 PC 上的应用，暂时不考虑网站"。将来若要共享，成品可走 deepWhale 站既有链路（R2 + 已备案域），**但本期不实现、不设计、不预留接口** |
| 调用在线生图 API | 无 API key；且本地 16G 卡够用，离线更省心 |
| **LoRA 训练** | **属于作者自己的准备事项，不进本工作台主线**。工作台只负责"能加载你训好的 LoRA"，不负责训、不管理训练数据集、不写训练脚本 |
| 素材采集 | 两个来源共 803 张，入池 415 张起步够用；夸克采集脚本现成，需要时另行执行 |
| 多用户 / 账号 / 权限 | 单人自用 |
| 上架发布流程 | 属于 deepWhale-MemeHub 的职责 |
| 商业授权 / 版权登记 | 沿用非商业二创立场，不做商业化配套 |
| 图片托管 / CDN / 图床 | 成品落本地磁盘 |

---

## 4. 架构

```
        ┌──────────────────────────────────────────┐
        │  本地 Web UI（浏览器，第二阶段实现）        │
        │  出片台 / 素材库 / 成品库 / 设置            │
        └───────────────────┬──────────────────────┘
                            │ HTTP + SSE（本机回环）
        ┌───────────────────▼──────────────────────┐
        │  lab/  （Python · 工作台本体）             │
        │                                          │
        │  assets.py      素材索引（415 张，只读引用）│
        │  workflows.py   工作流模板加载 + 参数注入   │
        │  comfy.py       调 ComfyUI HTTP API        │
        │  postprocess.py Pillow 去背 / 加字 / 缩放   │
        │  registry.py    登记册读写                  │
        │  cli.py         命令行入口                  │
        └──────┬────────────────────────┬──────────┘
               │ HTTP 127.0.0.1:8188    │ 文件读写
    ┌──────────▼──────────┐   ┌─────────▼──────────────────┐
    │  ComfyUI（外部进程）  │   │  output/YYYY-MM-DD/        │
    │  权重 + custom_nodes │   │  成品 + 本地完整记录        │
    └─────────────────────┘   └─────────┬──────────────────┘
                                        │ 定稿条目
                            ┌───────────▼──────────────────┐
                            │  data/registry.json（配方库） │
                            └──────────────────────────────┘
```

**分层职责**：

- **素材层**（`assets.py`）：只读扫描素材根目录，产出索引（路径 / 尺寸 / 是否动图 / 内容哈希）与重复组（sha1 → 9×8 dhash → 128×128 灰度 MAE 三级收敛）。**不复制、不移动、不修改素材原始文件**。
- **工作流层**（`workflows.py`）：加载 `workflows/*.json`（ComfyUI **API 格式**，不是 UI 格式），把业务参数映射到节点输入，产出可提交的 prompt 图。
- **引擎层**（`comfy.py`）：`POST /prompt` 提交、`GET /history/{id}` 轮询到完成、按 `/view` 取回文件；负责排队、超时、错误还原。**不启动 ComfyUI 进程**（假设它已在运行，见 §10）。进度订阅（WS `/ws`）留到真需要实时性的那天——轮询够用，也更好排队。
- **后处理层**（`postprocess.py`）：Pillow 做去背合成 / 加字 / 缩放 / 命名。去背的模型推理在 ComfyUI 里做（RMBG 节点），本层只做拼装。
- **登记层**（`registry.py`）：写 `output/…/registry.local.json`（全量）与 `data/registry.json`（定稿配方），见 §6。
- **表现层**（第二阶段）：FastAPI + 模板。

**明确不做的架构决定**：不做数据库（JSON 文件够）、不做任务队列（ComfyUI 自己有队列）、不做守护进程（用一次起一次）、不做插件系统。

---

## 5. 技术选型

| 层 | 选择 | 理由 |
|---|---|---|
| 语言 | **Python 3**（全栈） | 与 ComfyUI、训练、Pillow 同栈；作者已定"彻底统一到 Python" |
| 引擎 | **本地 ComfyUI**（`/home/xiao/workspace/comfyUI/ComfyUI`，conda env `comfyui`） | 已有环境，`127.0.0.1:8188` |
| 引擎接口 | **HTTP API**（不写自定义节点）；进度先用轮询，WS 留作以后 | 与 ComfyUI 版本解耦；工作流存成 JSON 可版本控制；不改 ComfyUI 本体 |
| 图像处理 | **Pillow** | 已装（12.1.1）；去背由 ComfyUI-RMBG 节点做，Pillow 只拼装 |
| 视频/GIF | **ffmpeg**（子进程调用） | 已装 8.0.1；GIF 调色板两遍法用 Pillow 做不好 |
| Web 框架 | **FastAPI**（第二阶段） | 全 Python；自带异步与 SSE，适合转发生成进度 |
| 前端 | **Jinja2 模板 + htmx + 少量原生 JS，零构建步骤** | 自用工具，不引入 Node 构建链（这是"全 Python"决定的自然延伸）；界面复杂度若将来超过阈值再议 |
| 配置 | **TOML**（`config/default.toml` 进 git，`config/local.toml` 不进） | 本机绝对路径不进仓库 |
| 依赖管理 | 运行时只依赖 **Pillow**（读图与后处理）；`lab doctor` 不碰它，纯标准库。`pyproject.toml` 只声明依赖与 pytest 路径 | 极简；不引 Poetry/uv，也不打包发版——工作台从仓库根直接跑 |

**为什么参考而不复用 deepWhale-MemeHub 的 `.cjs` 工具**：那套工具（`build-variants.cjs` / `dedupe.cjs`）的行为已经被读懂并作为规范参考，但技术栈不同（Node），直接复用会破坏"全 Python"的决策。去重环节**改用大肥鱼目录里现成的 Python 脚本**（`find_dupes.py` / `dupes_perceptual.py` / `verify_dupes.py`），净损失接近零。

---

## 6. 数据格式

### 6.1 两本账

| 账本 | 位置 | 进 git | 记什么 |
|---|---|---|---|
| **全量记录** | `output/YYYY-MM-DD/registry.local.json` | 否 | 每次生成的每一张（含废图），用于当天挑图 |
| **配方库** | `data/registry.json` | **是** | 只记**定稿**条目（你去背后、加字后、或手工标记保留的） |

理由：成品本身不进 git（几百 MB 图片），**但"什么参数能出什么图"是可重生成的知识**——这是本仓库里除了工作流 JSON 之外唯一值得版本控制的东西。定稿 ≈ 几十条量级，不会让仓库膨胀。

### 6.2 配方条目 schema

```jsonc
{
  "id": "fish-001",                    // 同一天内自增，格式 fish-###
  "createdAt": "2026-10-22T00:31:05+08:00",
  "kind": "sticker",                   // orig | sticker | captioned
  "file": "output/2026-10-22/fish-001.sticker.png",
  "width": 240, "height": 240,
  "animated": false,

  // 复现所需
  "workflow": "workflows/fish-txt2img.json",
  "model": "flux-2-klein-base-4b-fp8.safetensors",
  "prompt": "1girl, chibi, blue whale girl, ...",
  "negative": "",
  "seed": 123456,
  "steps": 50, "cfg": 3.5,
  "source": {                          // 为空表示纯文生图
    "type": "img2img",                 // img2img | reference
    "image": "/home/xiao/workspace/www/image/media/xxxx.webp",
    "denoise": 0.55
  },
  "caption": "你好呀",                  // 带字成图才有

  // 预留：本阶段不生成，字段占位
  "src": null, "preview": null, "large": null
}
```

- `id` / `width` / `height` / `animated` 沿用 deepWhale-MemeHub `stickers.json` 的同名语义，方便将来万一要上站时直接对接。
- **`src` / `preview` / `large` 三个字段本阶段恒为 `null`**——它们是"站点资源路径"，本工作台不做站。留着是零成本的语义占位，不是预留接口。

### 6.3 工作流模板与参数注入

`workflows/*.json` 使用 **ComfyUI API 格式**（`/prompt` 接口接受的结构）——每个节点一张卡片，靠节点 ID 互相引用：

```jsonc
{
  "4":  { "class_type": "CLIPTextEncode", "_meta": { "title": "提示词" },
          "inputs": { "clip": ["2", 0], "text": "" } },
  "7":  { "class_type": "Flux2Scheduler", "_meta": { "title": "步数" },
          "inputs": { "steps": 20, "width": 1024, "height": 1024 } },
  "10": { "class_type": "RandomNoise",    "_meta": { "title": "种子" },
          "inputs": { "noise_seed": 0 } }
}
```

**参数注入方式：按 `_meta.title` 查找节点，不按节点 ID。** 这是刻意的设计——节点 ID 会在你重排工作流时变化，标题不会。约定标题集合：`提示词` / `负面词` / `采样器` / `种子` / `步数` / `尺寸` / `底图` / `输出`。

**工作流怎么来**：直接**手写** API 格式 JSON（`workflows/fish-txt2img.json` 就是这么来的，13 个节点、链路线性），写完用 `lab.comfy` 的客户端提交验证。只有手写被引擎拒绝（节点类名或参数结构记错）时，才去界面拉一个官方模板导出对照——界面不是必经工序。

标题写错一个字，注入就会静默失效，所以 `tests/test_workflows.py` 把必需标题钉死。

### 6.4 素材索引

`assets.py` 扫描素材根目录生成的索引**不进 git**（作者已定：素材引用清单不入库）。索引落在**仓库内**的 `assets/index.local.json`（由 `.gitignore` 排除），本机有效——素材在 `/home/xiao/图片` 与 `~/workspace/www`，索引在仓库里，两者不同处。

---

## 7. 素材与资产策略

### 7.1 素材池

| 来源 | 路径 | 数量 | 构成 | 角色 |
|---|---|---|---|---|
| 夸克采集 | `/home/xiao/图片/deepseek大肥鱼表情包/DeepSeek鲸鱼娘表情包/` | **217** | 99 gif / 95 webp / 16 jpeg / 7 png（785 MB） | 素材池 |
| 选材池 | `/home/xiao/workspace/www/image/` | **586** | 435 webp / 110 png / 36 jpg / 4 gif / 1 jpeg（204 MB） | 素材池 |
| **合计** | | **803** | 约 1 GB | |

- `deepWhale-MemeHub/stickers/` 的 **383 张**是**已进站的成品**，只作风格与构图参考，**不作为底图素材**（它们是别人的成品，不是原始素材）。其中 **100 张动图**对理解"表情包该长什么样"有特殊价值。
- 大肥鱼目录下的 `重复检查/`（13 张比对拼图）与 `.Trash-0/`（2 张）是流程产物，**不是素材**，索引时要排除。
- **素材池当前挂载 415 张**：大肥鱼 217 + 素材库 `media/` 的 198，写在 `config/local.toml` 的 `paths.asset_roots` 里。素材库剩下的 388 张是 `large/` 与 `previews/` 的派生图，不作底图，所以不进池——**803 是「两个来源的总量」，415 是「工作台实际能挑的」**；体检与索引报的都是后者，别拿 803 去对它们。

### 7.2 三条铁律

1. **引用不复制**：工作台只在索引里存路径，不把素材拷进 `assets/`。素材原地不动，你的文件管理器视图不被打乱。
2. **素材不进 git**：素材约 1 GB，且版权归属不在本仓库，`assets/` 与索引全部排除。
3. **不修改素材**：任何清洗（去重、转格式）的产物另存，原文件只读。

### 7.3 合并去重

两个来源共 803 张，必然有重叠。去重的三级收敛实现在 `lab/assets.py` 里；那三个现成脚本（`find_dupes.py` / `dupes_perceptual.py` / `verify_dupes.py`）是**阈值来源**，不再被调用——理由见 ADR-0005。

```bash
python3 -m lab assets scan   # 扫素材、写索引、报张数与重复组
python3 -m lab assets dupes  # 按索引列出重复组明细
```

| 级别 | 判据 | 阈值来源 |
|---|---|---|
| 1 精确哈希 | sha1 字节一致 | `find_dupes.py` |
| 2 感知哈希 | 9×8 dhash 汉明距离 ≤ 4 | `dupes_perceptual.py` |
| 3 灰度比对 | 128×128 MAE：< 6 同一张 / 6–14 疑似 / ≥ 14 不同 | `verify_dupes.py` |

产出：本机索引（`assets/index.local.json`，不进 git），含 415 张素材、重复组与疑似组。**只报告，不删素材**——留哪张由人定。**去重不阻塞出图**：没有它也能出片，只是挑底图时会撞见重复。

---

## 8. 形象一致性策略

目标：**每一次生成的鲸鱼娘，看着都是同一只。**

按成本递增，分三道闸门——**前一道够用就不要开后一道**：

| 闸门 | 手段 | 成本 | 效果 | 定位 |
|---|---|---|---|---|
| 一 | **固定提示词骨架**：把角色描述（发色、瞳色、服饰、体型、画风）固化成一份可复用的 prompt 前缀 | 一次性写 20 分钟 | 低——跨图仍会漂 | 基线，永远开启 |
| 二 | **底图复用（img2img）**：从成品里挑一张最满意的当底图，`denoise` 控制在 **0.4–0.6**（高过 0.65 就丢形象，低于 0.35 就改不动表情） | 每次多一步选图 | 中高 | **第一阶段主力手段** |
| 三 | **参考图引导**：FLUX.2 Klein 原生支持多参考图（`<ref_image_0>` 语义），无需额外节点 | 调参 1–2 小时 | 高 | 当第二道仍不够稳时启用 |

**LoRA 不在这三道闸门里**——自训角色 LoRA 是作者自己的准备事项，训练完把文件丢进 `ComfyUI/models/loras/` 即可，工作台只负责加载。工作台不为它写代码。

**姿势与形象分开**：需要固定姿势时用 ControlNet（只锁姿势，不锁身份），必须与上面某一道叠加使用，不能单独用。

---

## 9. GUI 大致设计（第二阶段实现，本阶段只设计）

> 形态已定：**本地 Web UI**——Python 起本地服务，浏览器打开。GUI 实现排在管线之后（等能出片了再包壳）。

### 9.1 技术选型

- **后端**：FastAPI（同一进程内直接 import `lab/`，不另起 API 层）
- **前端**：Jinja2 模板 + htmx（局部刷新）+ 少量原生 JS（进度条）
- **零构建步骤**：没有 npm、没有打包器、没有 `node_modules`。改完 HTML 刷新浏览器即可
- **生成进度**：后端轮询 ComfyUI 的 `/history/{id}`，再用 **SSE** 推给浏览器（图出到第几张、当前第几步）

### 9.2 信息架构

```
鱼片加工厂
├── 出片台  Generate     ← 主页面，90% 时间在这
├── 素材库  Assets        ← 415 张素材池，挑底图
├── 成品库  Gallery       ← 历史成品 + 配方库
└── 设置    Settings      ← ComfyUI 地址、工作流、路径
```

### 9.3 页面清单

**① 出片台（主页面）**

| 区域 | 内容 |
|---|---|
| 左栏 · 输入 | 台词文本框；底图区（可从素材库送来 / 拖入 / 留空 = 纯文生图）；形象选择（提示词骨架预设）；风格选择；张数（1/2/4/8）；seed（留空=随机，可锁定）；重绘强度滑块（仅选底图时出现，默认 0.55） |
| 右栏 · 结果网格 | 每张图一张卡片：缩略图 / seed / 三个动作按钮（**去背** / **重跑这张** / **保留**） |
| 顶部 · 状态条 | ComfyUI 连通性指示灯；队列长度；本次任务的进度（"3/8 已完成 · 第 4 张第 20/50 步"） |
| 底部 · 批次动作 | 「全部去背」「导出保留项」「清空本次」 |

**② 素材库**：瀑布流网格（懒加载）+ 顶部筛选（来源批次 / 静图·动图 / 只看未用过）+ 每张图悬停显示「送去出片台」；点击开大图，大图页有「设为底图」「设为参考图」。

**③ 成品库**：按日期分组（对应 `output/YYYY-MM-DD/`）；每条显示缩略图 + 配方摘要（model / seed / steps）；支持按 prompt 关键词、seed、模型搜索；动作：「用同参数重跑」（跳回出片台并填好全部参数）、「导出」、「查看登记项」。

**④ 设置**：ComfyUI 地址（默认 `127.0.0.1:8188`）+ 「测试连接」按钮；默认工作流选择（下拉列出 `workflows/*.json`）；素材根目录列表（可增删）；输出目录；主题明暗。

### 9.4 三条关键交互流

**流 A · 从零出一张（最常用）**

```
打开出片台 → 输入台词"你好呀" → 选形象预设 → 张数 4 → 点「开始生成」
  → 结果网格逐张出图（顶部状态条走进度）
  → 挑中一张 → 点「去背」→ 该卡片原地变成透明底预览（棋盘格背景）
  → 点「保留」→ 写入配方库
  → 点「导出」→ 素材落地 output/ 并弹出文件夹
```

**流 B · 用素材做底图（锁形象）**

```
素材库 → 筛"静图" → 挑一张像鲸鱼娘的 → 「送去出片台」
  → 出片台底图区已填入，重绘强度自动设为 0.55
  → 改台词 → 生成 → 形象明显更稳
```

**流 C · 复现一周前那张**

```
成品库 → 按 seed 搜索 → 找到 → 「用同参数重跑」
  → 出片台全部参数（prompt/seed/model/底图/重绘强度）已填好
  → 直接生成 → 得到同款
```

### 9.5 明确不在 GUI 里的东西

- 不做工作流编辑器（要改工作流请去 ComfyUI 界面，导出后放回 `workflows/`）
- 不做 LoRA 训练界面
- 不做用户登录 / 权限
- 不做移动端适配（自用，PC 浏览器）
- 不做实时协作 / 分享链接

---

## 10. 环境前提

### 10.1 现状（2026-10-22 实测）

| 项 | 状态 |
|---|---|
| GPU | RTX 5070 Ti，**16303 MiB (16 GB)**，sm_120，驱动 595.99.02，CUDA 13.2 |
| ComfyUI 代码 | `/home/xiao/workspace/comfyUI/ComfyUI`（168 MB，纯代码） |
| ComfyUI 运行时 | conda env `/home/xiao/miniconda3/envs/comfyui`，torch 2.12.0+cu130，`cuda.is_available() = True` |
| **生图权重** | **零**（`models/` 26 个子目录全空；全盘那 4 个 `.safetensors` 是 MinerU 与 compressed_tensors 的测试文件，与生图无关） |
| **custom_nodes** | 已装 ComfyUI-Manager、ComfyUI-RMBG、ComfyUI-KJNodes（见 #3） |
| 服务状态 | 8188 未监听（ComfyUI 未运行） |
| 启动方式 | `conda activate comfyui` → `python main.py` → `127.0.0.1:8188`（见 `/home/xiao/workspace/comfyUI/记录.md`） |
| 工具链 | ffmpeg 8.0.1、ImageMagick 7.1.2、Python + Pillow 12.1.1、Node v24.16.0、pnpm 11.28.5 |
| 磁盘 | 可用 **455 GB** |
| 参考教程 | `/home/xiao/视频/ComfyUICourse/`（EP01–EP22，含 FLUX.2 Klein、Inpainting、Upscale） |

### 10.2 待补齐（第一阶段的主要工作）

**基座模型：FLUX.2 Klein 4B**（Apache-2.0，可商用）

| 文件 | 体积 | 放置目录 |
|---|---|---|
| `flux-2-klein-base-4b-fp8.safetensors` | 4.09 GB | `models/diffusion_models/` |
| `flux-2-klein-4b-fp8.safetensors`（蒸馏 4 步版） | 4.07 GB | `models/diffusion_models/` |
| `qwen_3_4b.safetensors` | 8.04 GB | `models/text_encoders/` |
| `flux2-vae.safetensors` | 0.34 GB | `models/vae/` |
| **合计** | **≈ 16.5 GB** | |

- 官方显存要求：distilled 8.4 GB / base 9.2 GB → 16 GB 卡**可行**。
- 这份 `qwen_3_4b.safetensors` 同时可给 Z-Image-Turbo 用，将来若加 Z-Image 不要重复下载。
- **跳过 Anima**（2B 动漫专精、仅 5.6 GB、16G 卡极轻松，但社区 LoRA 生态薄，与"形象锁定"的长期目标不匹配）。仅在 Klein 4B 在实际机器上表现不及预期时，才作为备选回头启用。

**custom_nodes**

| 节点 | 用途 | 优先级 |
|---|---|---|
| `Comfy-Org/ComfyUI-Manager` | 节点与模型管理，必装 | P0 |
| `1038lab/ComfyUI-RMBG` | 去背（贴纸核心能力），v3.3.0 | P0 |
| `kijai/ComfyUI-KJNodes` | 通用工具节点 | P1 |
| `Fannovel16/ComfyUI-Frame-Interpolation` | 动图插帧（表情包动图时需要） | P2 |

**去背模型**：优先 **Lucida**（BiRefNet 微调，MIT，专攻插画 / 线稿 / 贴纸 / logo / 发光特效），次选 BiRefNet 通用版。多数模型首次使用时 ComfyUI-RMBG 会自动下载。

**下载通道**：
- `hf-mirror.com` 本机可达（HTTP 200），`huggingface.co` 直连也通。
- **坑**：`hf-mirror` 的 `/api/models/...` 路径被代理拦截、返回空——所以 `huggingface-cli` 的"列出仓库文件"会失败。改用指定具体文件名的下载方式，或直接拼 `https://hf-mirror.com/<repo>/resolve/main/<path>`。
- `github.com` 可达；clone custom_nodes 超时时用 GitHub 代理前缀。

### 10.3 明确假设

**工作台不负责启动 ComfyUI。** 假设你在需要出图前已经把它跑起来（`conda activate comfyui && python main.py`）。工作台只做连通性检测与友好报错（"8188 连不上，请先启动 ComfyUI"）。这是刻意的边界：进程生命周期管理牵扯太多平台差异，收益不成比例。

---

## 11. 版权与许可立场

- **立场**：沿用 fisharchive 取证结论定的**非商业二创**（上游 `EDMOK/blue-fish-archive` 无 LICENSE、原作 CC BY-NC-SA 4.0 语境）。
- **产出标注**：所有定稿产出在登记项里标注「AI 生成 · 非商业二创」，导出时不强制加水印，但元数据里保留。
- **模型许可**：FLUX.2 Klein 4B 是 **Apache-2.0，可商用**。所以"非商业"在这个项目里**不是被迫的许可约束，而是作者主动选择的立场**——这一点值得记下来：将来若想改成立场，唯一的阻碍是上游素材的版权，不是模型许可。
- 不引入任何需要商业授权的模型（因此排除 FLUX.2 Klein 9B、FLUX.3 klein 等 gated / 非商用权重）。

---

## 12. 目录结构

```
deepfish-meme-lab/
├── docs/
│   ├── design.md              ← 本文件
│   ├── roadmap.md             ← 阶段与验收
│   ├── research/              ← 调研落盘（带引用的 Markdown）
│   ├── adr/                   ← 不可逆的决策记录
│   └── agents/                ← deck 插件生成 + Agent 约定
├── lab/                       ← Python 包（工作台本体）
├── tests/                     ← pytest，就地红绿
├── workflows/                 ← ComfyUI 工作流模板（API 格式 JSON）★ 核心资产
├── data/
│   └── registry.json          ← 配方库（定稿条目，进 git）★ 核心资产
├── config/
│   ├── default.toml           ← 进 git
│   └── local.toml             ← 不进 git（本机绝对路径）
├── scripts/                   ← 一次性脚本（干净来、干净走）
├── assets/                    ← 不进 git：素材索引（指向 415 张素材池）
├── output/                    ← 不进 git：成品 + 完整记录
├── pyproject.toml             ← 声明依赖、Python 版本下限与 pytest 路径，不打包
├── GLOSSARY.md                ← 术语表
└── .gitignore
```

**两个★是仓库里真正值钱的东西**：工作流 JSON 是你调了几个小时换来的，配方库是"什么参数能出什么图"的记录。图片可以重生成，这两样丢了要重来。

---

## 13. 术语

本项目特有的词汇统一定义在 **[GLOSSARY.md](../GLOSSARY.md)**——它只定词，不含实现细节；不可逆的架构决策记在 **[docs/adr/](./adr/)**。

新增或改动术语请改 GLOSSARY，不要在这里另起一张表：两处维护必然分叉。
