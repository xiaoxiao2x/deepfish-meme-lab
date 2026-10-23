# 鱼片加工厂 · 路线图

> 项目代号 `deepfish-meme-lab` ｜ 中文名：**鱼片加工厂**
> 本文回答"**先做什么**、**做到什么程度算成**"；"是什么 / 不做什么"见 [design.md](./design.md)

---

## 0. 阶段划分

只有一个阶段：**第一阶段 · 能出片**。它包含 5 个里程碑，**GUI 实现是第 5 个**——按已确认的决定，GUI 先出设计（已在 design.md §9 完成）、实现排在管线跑通之后。

| 里程碑 | 内容 | 产出 | 依赖 |
|---|---|---|---|
| **M1** | 环境补齐 | 权重 + 节点就位，ComfyUI 能画 Klein | — |
| **M2** | 工作流与管线 | `python -m lab make` 能出图 | M1 |
| **M3** | 后处理 | 去背 + 加字 + 导出 | M2 |
| **M4** | 登记册 | 配方可复现 | M2 |
| **M5** | GUI | 浏览器里出片 | M3 + M4 |

**M2 与 M3 可以并行**（M3 的输入只需一张静态 PNG，可以先拿任何图片调试）。M1 下载的 16.5 GB 期间正好用来写 M2/M3 的代码骨架。

---

## 1. M1 · 环境补齐

### 做什么

**1) 下载基座权重**（FLUX.2 Klein 4B，共 ≈ 16.5 GB）

放到 `/home/xiao/workspace/comfyUI/ComfyUI/models/` 下：

| 文件 | 目标目录 |
|---|---|
| `flux-2-klein-base-4b-fp8.safetensors` | `diffusion_models/` |
| `flux-2-klein-4b-fp8.safetensors` | `diffusion_models/` |
| `qwen_3_4b.safetensors` | `text_encoders/` |
| `flux2-vae.safetensors` | `vae/` |

通道：`hf-mirror.com`（本机可达）。**注意坑**：`hf-mirror` 的 `/api/models/...` 被代理拦截返回空，所以 `huggingface-cli` 的"列文件"会失败——直接指定文件名下载，或拼 `https://hf-mirror.com/<repo>/resolve/main/<path>`。

**2) 装 custom_nodes**

```bash
cd /home/xiao/workspace/comfyUI/ComfyUI/custom_nodes
git clone https://github.com/Comfy-Org/ComfyUI-Manager
git clone https://github.com/1038lab/ComfyUI-RMBG
git clone https://github.com/kijai/ComfyUI-KJNodes          # 可选
```

`ComfyUI-RMBG` 有 pip 依赖，需装进 `comfyui` conda 环境。

**3) 首次启动与自检**

```bash
conda activate comfyui
cd /home/xiao/workspace/comfyUI/ComfyUI
python main.py          # → 127.0.0.1:8188
```

无 tty 的环境（agent 的 shell）跑不了 `conda activate`，用 `./scripts/comfy.sh start`：它走绝对路径解释器 + `setsid` + 日志重定向，等到 8188 就绪才返回。

### 完成标志

- ComfyUI 启动无报错，界面能打开
- 节点列表里能看到 `RMBG (Remove Background)` 节点
- 用一个最简 Klein 工作流能画出一张图（哪怕是一只猫）

### 已探明的事实（不用重查）

- ComfyUI 版本 **0.42.0**（2026-10-22 实测；本机已原生支持 FLUX.2 Klein，无需升级——需 ≥0.41.0 的只有 FLUX.3 Klein，本项目不用）
- **本地版本已原生支持 FLUX.2 与 Klein 文本编码器**（`comfy/supported_models.py` 有 `Flux2` 类与 `KleinTokenizer` / `klein_te`）→ **不需要先升级 ComfyUI 就能用 Klein 4B**
- FLUX.**3** Klein 才需要 ComfyUI ≥ 0.41.0——本项目不用它，无关
- 中文字体已齐：89 个中文字体，`Noto Sans CJK` 系列（Black/Bold/Regular/Medium/Light）在 `/usr/share/fonts/opentype/noto/` → 加字的中文渲染无障碍

### 风险

- **显存**：官方标称 distilled 8.4 GB / base 9.2 GB，但 VAE 解码峰值可能更高（16 GB 卡）。**建议先跑 distilled 4 步版**建立信心，再试 base 50 步版。
- 若两者都在 16 GB 上不稳，退路是：调小分辨率（1024 → 768）、开启分阶段 offload、或回头启用备选的 Anima（2B，5.6 GB）。
- `custom_nodes` 的 git clone 在国内可能超时，需用代理前缀。

---

## 2. M2 · 工作流与管线

### 做什么

**1) 写两个工作流模板**

直接**手写** API 格式 JSON（不是界面导出），关键节点写上 `_meta.title`，存进 `workflows/`：

| 文件 | 用途 | 必需标题 |
|---|---|---|
| `workflows/fish-txt2img.json` | 纯文生图 | `提示词` `负面词` `采样器` `种子` `步数` `尺寸` `引导强度` `输出` |
| `workflows/fish-img2img.json` | 底图复用（锁形象） | 上述 + `底图` + `重绘强度` |

标题约定见 design.md §6.3——**按 `_meta.title` 注入参数，不按节点 ID**。
标题写错会静默失效，所以 `tests/test_workflows.py` 把它钉死。

**纯文生图已跑通（2026-10-23）**：13 个节点，双版本实测 ——

| 版本 | 步数 | 1024×1024 一张 | 显存峰值 |
|---|---|---|---|
| `flux-2-klein-4b-fp8`（distilled） | 4 | 2.5 秒 | 12.8 GB |
| `flux-2-klein-base-4b-fp8`（base） | 20 | 10.0 秒 | 13.4 GB |

**2) 写 `lab/` 的模块**

```
lab/
├── config.py       读 config/default.toml + local.toml，深合并
├── doctor.py       环境体检（六项检查，只读为主）
├── cli.py          命令行入口
├── paths.py        素材后缀表与排除规则（零依赖，体检与索引共用）
├── assets.py       素材扫描与索引（只读引用）
├── comfy.py        ComfyUI HTTP 客户端（提交 / 轮询 / 取回）
├── stub.py         假引擎（无显卡也能端到端调试）
├── workflows.py    模板加载 + 参数注入
├── contract.py     模板与引擎的节点清单对账（纯规则，不联网）
├── pipeline.py     串起出图：套模板 → 提交 → 等 → 取回 → 按 fish-NNN 落盘
└── postprocess.py  后处理：去背出透明底贴纸
```

（`config.py`、`doctor.py`、`cli.py`、`paths.py`、`assets.py`、`comfy.py`、`stub.py` 已落地 —— 见 #5、#13、#6；`workflows.py` 与 `pipeline.py` 见 #7；`postprocess.py` 的去背见 #9；`registry.py` 按 #11。）

**3) `lab doctor`：环境体检**

第一个要写的命令，比 `make` 更早。检查六项：ComfyUI 连通性、4 个权重文件是否在位、`custom_nodes` 是否装齐、模板与引擎的节点清单对不对得上、素材根目录是否可达、输出目录是否可写。**没有它，后面每个错误都要用十分钟去猜。**

最后那一项（`contract.py` + `doctor.check_templates`）是 #9 之后补的：模板是**手写**的 API 格式 JSON，节点类名、输入键、连线的类型与下标、下拉候选值（模型名、采样器名）任何一处写错，都要等提交时才被引擎拒；现在体检就报，还顺带说出"引擎认的是什么"。

### 完成标志

```bash
python -m lab doctor
# 全绿

python -m lab make --prompt "chibi blue whale girl, sticker style" --count 4
# → output/2026-10-22/ 下出现 4 张 PNG，终端打印各自路径与 seed
```

**已达成（2026-10-23，见 #7）**：`--count 4` 在 base 4B / 20 步下 92 秒出 4 张，命名 `fish-001.orig.png`…；同 seed 同参数两次跑出**字节相同**的图；把模板节点 ID 全部重排后参数仍准确落到对应节点（从引擎 `/history` 回读执行图核对）。「第 N 步」的实时进度拿不到——那需要订阅 `/ws` 的 `progress` 消息，当前只报已等秒数。

### 风险

- **工作流脆性**：节点 ID 会变、`_meta.title` 可能被 ComfyUI 重写、不同版本导出格式有细微差异。缓解：`lab doctor` 的「模板契约」一项对着引擎的 `/object_info` 核对（节点类、输入键、连线的类型与下标、下拉候选值），`tests/test_workflows.py` 钉死必需标题，`scripts/check_docs.py` 保证 design.md 的标题集合与 `lab.workflows` 的常量两处一致。
- ComfyUI 的输出是写到它自己的 `output/` 目录的，工作台需要从 `/history` 拿到文件名后**搬运或引用**到自己 `output/` 下。搬运策略要早定（建议：搬运，不引用——ComfyUI 的 output 目录你迟早会清）。

---

## 3. M3 · 后处理

### 做什么

| 功能 | 手段 |
|---|---|
| **去背** | 工作流里接 `BiRefNetRMBG` 节点（模型 Lucida、`background=Alpha`），输出 RGBA PNG —— **已达成**（#9） |
| **加字** | Pillow + `NotoSansCJK-Black.ttc`；自动换行、描边、位置微调 —— **已达成**（#10） |
| **规格导出** | 目前只做"原图（1024 级）+ 透明底贴纸 PNG + 带台词成图"三种；**表情包规格（240×240 等）属于非目标，不在本阶段** |
| **命名与落盘** | `output/YYYY-MM-DD/fish-###.{orig|sticker|captioned}.png` |

CLI：

```bash
python -m lab sticker <图片路径>                        # → 透明底 PNG
python -m lab caption <贴纸路径> --text "你好呀"        # → 带字成图
```

### 完成标志

对同一张图能依次跑出：原图 → 透明底贴纸 → 带字成图，中文台词渲染正确、无豆腐块。

**去背已达成（2026-10-23，见 #9）**：`python -m lab sticker <图片>` 出一张 RGBA 贴纸 —— 源图是工作台出的就落在它旁边（沿用同一个序号：`fish-007.orig.png` → `fish-007.sticker.png`），别的图落当天目录按当天最大号接；成品落盘前回读验一遍 alpha：接成下标 1 的 `MASK` 引擎在提交时就用 `Return type mismatch` 拒了，接成下标 2 的 `MASK_IMAGE` 能跑完但透明区会丢 —— 回读这一关当场报错，不会悄悄出一张不透明的"贴纸"。

**加字已达成（2026-10-23，见 #10）**：`python -m lab caption <贴纸> --text "你好呀"` 出一张 `.captioned.png` —— 同样沿用贴纸的序号、落在贴纸旁边（`fish-007.sticker.png` → `fish-007.captioned.png`）；台词自动折行、压在下沿居中、字色深灰 `#1B1B1B` 配一圈 4px 白描边，参数在 `config/default.toml` 的 `[postprocess]` 里改。这一步不碰引擎，纯 Pillow，跑一次是毫秒级的。判据 ② 与 ⑥ 是这一票的重点：**缺字体**（配置里的路径不存在、机器上一个中文字体都没有）与**缺字形**（这个字体里没有那个字，包括 emoji）都在画之前报错并说是哪个字、哪个 `U+XXXX`，不会安安静静画成方块 —— 靠"拿 `U+FFFF` 的遮罩当豆腐块指纹"来判，测过 DejaVuSans 缺汉字的遮罩和 `U+FFFF` 完全一致。规格导出还没做。

### 风险

- **首次去背要下权重**：Lucida（约 1 GB）第一次用到时由 ComfyUI-RMBG 自己下载到引擎的 `models/RMBG/BiRefNet/`。本机环境里 `all_proxy=socks://…` 与 `no_proxy` 里的 `[::1]` 会让 httpx 报 `Unknown scheme for proxy URL` / `Invalid port: ':1]'`，于是"下载不下来"看起来像"模型坏了"——换一份干净代理环境单独下即可（`scripts/comfy.sh start` 已顺手清掉这两个变量）。
- **去背节点的 optional 一个都不能少**：`BiRefNetRMBG` 的 `mask_blur` / `mask_offset` / `invert_output` / `background` 声明成 optional，但节点的 `process_image` 用 `params["键"]` 直接取 —— 靠 API 提交时少写一个键就 KeyError（`Error in image processing: 'mask_blur'`）。模板里八个键全写上，值就是节点默认值。
- **BiRefNet 要 timm**：引擎的 conda 环境起初没装 `timm`，第一次去背才报 `Error loading BiRefNet model: No module named 'timm'`（`pip install timm` 即可）。它不属于 `comfy.required_nodes` 能查出来的东西。
- **动图去背**：需要逐帧处理，成本与复杂度高。**本阶段明确不做**——动图（100 张 gif 素材、动图表情包）留到本阶段之后。
- 中文字体在不同字号下的字重选择（Black 做贴纸标题、Regular 做正文），需要试（#10 先用 Black 一个字号跑通，字重选择留给后面调）。
- 描边与背景对比度：透明底上加了字，导出到聊天软件里可能看不清——需要一层白描边（#10 已做成 4px 白描边，默认值可调）。

---

## 4. M4 · 登记册

### 做什么

两本账（design.md §6.1）：

- `output/YYYY-MM-DD/registry.local.json`：全量记录（含废图），不进 git
- `data/registry.json`：**配方库**，只记定稿，进 git

```bash
python -m lab keep <图片路径>       # 标记保留 → 写入配方库
python -m lab gallery               # 列出配方库（表格）
python -m lab reproduce fish-001     # 用配方重跑，验证可复现性
```

### 完成标志

`python -m lab reproduce fish-001` 能重新产出一张视觉上与原图一致的图（同 seed + 同模型下应当完全一致）。

**两本账已达成（2026-10-24，见 #11）**：出片 / 去背 / 加字三站各自在落盘那一行之后写一笔当天账 `output/<日期>/registry.local.json` —— 一张一落账，不攒到整批结束（这张已经成了，就不该被下一张的失败带走）；条目带种子、步数、引导强度、模板路径、模型文件名与引擎版本，去背与加字这两站不碰引擎或换不了参数，就按同号去当天账里借上一步那一整条，引擎版本因此不会在链条后半段被抹成「未知」。`python -m lab keep <图片>` 是**动作**：照着这张图回当天账里找回它那一条，把整条配方搬进 `data/registry.json`，只改落点（图挪过位置也认得，位置认得认不出了就退到「号 + 阶段」）；账里真没有它的来路时仍然收下这张图、生成那几栏留空，并当场说明没有来路 —— 比编一个种子诚实。`python -m lab gallery` 按时间倒序列出号 / 阶段 / 模型 / 种子 / 留的时间。`output/` 整目录在 `.gitignore` 里、`data/registry.json` 不在，判据 ④ 是这条 ignore 规则的自然结果。同一天多批生成时的编号冲突靠「读当天的账 + 目录里已有的最大号」一起算，所以废图被清走之后号也不会回收。**reproduce 还没做，见 #12。**

### 风险

- 配方库增长后的 JSON 冲突（单人用，风险低；真要处理时再迁移）
- `reproduce` 的一致性依赖 ComfyUI 确定性——同 seed 同参数在不同 ComfyUI 版本下**可能不同**，所以配方里必须记 `comfyui_version` 字段（**M2 时就要加，别等到 M4 才想起来**）

---

## 5. M5 · GUI

设计与技术选型已定稿（design.md §9），这里只排任务：

| 步骤 | 内容 |
|---|---|
| M5.1 | FastAPI 骨架 + 四个页面路由（出片台 / 素材库 / 成品库 / 设置）+ 静态资源 |
| M5.2 | **出片台**：参数面板 + 结果网格 + 生成进度（SSE）—— 这是 90% 的价值所在，先做 |
| M5.3 | **成品库**：读配方库 + 缩略图 + "用同参数重跑" |
| M5.4 | **素材库**：415 张瀑布流 + 筛选 + "送去出片台" |
| M5.5 | **设置**：ComfyUI 地址与连接测试、工作流选择、路径配置 |

### 完成标志

浏览器打开 `http://127.0.0.1:PORT`，能在出片台输入台词、看到图逐张出现、点去背、点导出——**全程不碰终端，也不开 ComfyUI 界面**。

---

## 6. 第一阶段验收标准（Definition of Done）

一句话版（已确认）：

> 一条命令/一个页面，输入一句台词或一张底图 → ① 原图（1024 级）② 透明底贴纸 PNG ③ 带台词成图。

可执行版：

```bash
python -m lab make --caption "你好呀" --count 4
python -m lab sticker output/2026-10-22/fish-001.png
python -m lab caption output/2026-10-22/fish-001.sticker.png --text "你好呀"
```

全部通过条件：

1. 三件成品都落盘、格式正确（③ 的中文正确渲染）
2. **连跑 5 次，鲸鱼娘形象稳定在同一只**
3. **无手 / 眼崩坏**（明显畸形即不通过）
4. **全程不需要打开 ComfyUI 界面**（只允许它作为后台进程运行）
5. 配方库里能查到这几条记录，且 `reproduce` 能复现

---

## 7. 阶段推进规则：痛点驱动，不排日期

**不设 deadline。** 下一件事什么时候开始，由痛点出现决定：

| 触发信号 | 启动 |
|---|---|
| 管线能稳定出片，但你开始嫌"命令行里挑图太麻烦" | M5（GUI） |
| 形象仍然不稳，img2img 调到 0.65 也不够 | 参考图引导（design.md §8 第三道闸门） |
| 想要动图表情包 | 动图管线（逐帧去背 + ffmpeg 合成） |
| 想要固定姿势 | ControlNet |
| 自己训好了角色 LoRA | 在工作流里挂 LoraLoader 节点，工作台只负责加载 |
| 想让朋友用 | 改监听地址（`--host 0.0.0.0`），或考虑上站 |

**反面规则**：如果某个痛点在连续两周内没有真实出现，就不要提前做它。这份路线图的价值在于**阻止提前动手**。

---

## 8. 明确的未来项（本阶段不做，也不排期）

- 动图表情包（逐帧去背、插帧、ffmpeg 合成 GIF/WebP）
- 表情包规格导出（240×240 等微信规格）
- ControlNet 姿势锁定
- 参考图引导（Klein 多参考）
- 素材继续采集（夸克网盘脚本现成）
- LoRA 训练（**作者自己的准备事项，不进本工作台主线**）
- 上站 / 共享（deepWhale 站既有链路）
- 多用户、账号、权限

---

## 9. 已知的坑（动手前先读）

1. **`hf-mirror` 的 `/api/models/` 路径不可用**——返回空，被代理拦。下载只能用 resolve 直链或指定具体文件名。
2. **ComfyUI 的输出会落在它自己的 `output/`**——工作台要主动搬运，别指望它写到你指定的目录。
3. **工作流节点 ID 会漂**——一律用 `_meta.title` 定位。手写的模板先用 `lab doctor` 对着引擎的 `/object_info` 对一遍：节点包没装齐、输入键抄错、下拉里没有那个候选值，它当场就说，不用等到提交吃 400。
4. **同 seed 不保证跨版本一致**——配方里记 `comfyui_version`。
5. **16 GB 是"够用"不是"宽裕"**——同时开浏览器、编辑器、GUI 服务时，VAE 解码峰值可能顶上去。出图时别开别的大内存程序。
6. **素材是引用不是复制**——移动或重命名原始素材目录会让索引失效。索引里存路径，路径变了要重扫。
7. **`assets/` 与 `output/` 不进 git**——别试图 `git add -f`，1 GB 素材和不断增长的成品会把仓库搞烂。
