# 本地角色生图选型调研

> 记录日期：2026-10-22 ｜ 调研方式：**只读**（未下载任何权重、未安装任何节点、未跑过一张图）
> 用途：`docs/roadmap.md` M1「环境补齐」的执行依据。**本文的数字都是纸面数据，未在 5070 Ti 上实测过**——实测结果应回写到本文。

## 结论

三行：

1. **基座选 FLUX.2 Klein 4B**（Apache-2.0、非 gated、≈16.5 GB），官方显存要求 distilled 8.4 GB / base 9.2 GB → 16 GB 卡可行。
2. **形象锁定走 img2img 起步，自训角色 LoRA 作为作者侧准备**（工作台只负责加载，见 design.md §8）。
3. **只下这四个文件**，别顺手多下；其中 `qwen_3_4b.safetensors` 与 Z-Image-Turbo 共用，将来加 Z-Image 不要重复下载。

## 候选基座对比（16 GB 显存约束下）

| 模型 | 参数量 | 体积（含文本编码器与 VAE） | 许可 / gated | 备注 |
|---|---|---|---|---|
| **FLUX.2 Klein 4B** | 4B | **≈16.5 GB**（distilled 4.07 + base 4.09 + qwen_3_4b 8.04 + vae 0.34） | **Apache-2.0，非 gated** | 生态最强：AI Toolkit 有训练参考实现、一致性 LoRA、**多参考图原生支持** |
| Anima | 2B | ≈5.6 GB（4.18 + 1.19 + 0.25） | other，非 gated | 动漫/非照片插画专精，16 G 极轻松；**社区 LoRA 生态薄**，与"形象锁定"的长期目标不匹配 |
| Z-Image-Turbo | 6B | ≈20.7 GB（12.30 + qwen_3_4b 8.04 共用 + 0.34） | Apache-2.0，非 gated | 8 NFE；需分阶段 offload；有 ControlNet Union（3.10 GB → `models/model_patches/`） |
| NewBie-image-Exp0.1 | 3.5B | ≈15.9 GB（6.97 + 7.77 + 1.12） | — | Xml 结构化提示词 + Danbooru 标签；**贴 16 GB 上限** |
| Qwen-Image-Edit / Qwen-Image-2512 | — | 30 GB 级 | Apache-2.0 | 需 offload，超出舒适区 |
| FLUX.2 Klein 9B、FLUX.3 klein | 9B | — | **gated / 非商用** | 与"不引入需商业授权的模型"冲突，排除（FLUX.3 另需 ComfyUI ≥ 0.41.0） |
| SDXL 系（Illustrious / NoobAI / Animagine） | — | — | **2026 年许可雷区** | 无体积优势，不作为新工程起点 |

**跳过 Anima 的理由**：它更轻，但生态薄；Klein 4B 在 16 GB 上同样跑得动，且多参考图与 LoRA 训练链都是现成的。只有在 Klein 4B 实测不及预期时才回头启用 Anima。

## 一致性方案

| 方案 | 上手成本 | 效果 | 备注 |
|---|---|---|---|
| 固定提示词骨架 | 20 分钟 | 低，跨图仍漂 | 基线，永远开启 |
| **img2img 底图复用** | 每次多一步选图 | 中高 | **第一阶段主力**，`denoise` 0.4–0.6（>0.65 丢形象，<0.35 改不动表情） |
| 参考图引导 | 1–2 小时调参 | 高 | Klein **原生** `<ref_image_0>` 多参考 + Identity Feature Transfer + 一致性 LoRA，无需额外节点 |
| 自训角色 LoRA | 作者侧准备 | 最高 | 过拟合与风格污染是真风险，甜蜜点窗口窄 |
| ControlNet | — | 只锁姿势不锁身份 | **必须与上面某一道叠加**，不能单独用 |

SDXL 路线若启用参考图引导，需要 `cubiq/ComfyUI_IPAdapter_plus` + InsightFace/ONNX，且 IPAdapter 权重 0.7–0.8 最佳（1.0 过硬、0.5 丢相似度）。Klein 路线不需要。

## 自训 LoRA 的实测参考

来源：一份 RTX 5070 Ti 16 GB + 64 GB RAM 的实测记录（<https://mnya.tw/cc/word/2697.html>，2026-01-14）。

| 项 | 值 |
|---|---|
| 素材量 | 15 张，全部使用同一条训练提示词 |
| 工具 | **AI Toolkit by Ostris**（非 kohya） |
| 关键设置 | Low VRAM + Transformer/Text Encoder float8 + Batch 2 + AdamW8Bit + lr 1e-4 + 分辨率 512 + Cache Latents |
| 必须项 | 挂上官方 `training_adapter` |
| 峰值显存 | ≈14–15 GB（91.8%） |
| 3000 步耗时 | ≈1.5 小时 |
| 产物 | LoRA ≈81 MB |
| 甜蜜点 | **1500–2100 步**（该实测取 1800） |
| 出图速度 | 1280×960 → 12–15 秒 |
| 硬件底线 | ≥12 GB VRAM、≥32 GB RAM |
| 素材下限 | 最低 10–15 张，推荐 25–40 张 |

工具选型：SD1.5/SDXL → kohya_ss；**FLUX.2 / klein → AI Toolkit**；多模型混用 → OneTrainer。

> 注意：这是**别人的机器、别人的模型**上的数字，只作量级参考。本机实测前不要把它当承诺。

## custom_nodes

| 节点 | 用途 | 优先级 |
|---|---|---|
| `Comfy-Org/ComfyUI-Manager` | 节点与模型管理 | P0（必装） |
| `1038lab/ComfyUI-RMBG` | 去背，v3.3.0（2026-10-20） | P0（贴纸核心能力） |
| `kijai/ComfyUI-KJNodes` | 通用工具节点 | P1 |
| `Fannovel16/ComfyUI-Frame-Interpolation` | 动图插帧（RIFE/FILM） | P2 |
| `cubiq/ComfyUI_IPAdapter_plus` | 一致性 | **不需要**（仅 SDXL 路线） |

`ComfyUI-RMBG` 有 pip 依赖，装完需要在 ComfyUI 的 conda 环境里补装。

## 去背

节点：`RMBG (Remove Background)`（分类 `🧪AILab/🧽RMBG`）。

模型优先级：**Lucida**（BiRefNet 微调，MIT，专攻插画/线稿/贴纸/logo/发光特效）→ BiRefNet（通用）→ FeyNoBg（Swin-L 263M，发丝/蕾丝/半透明）→ RMBG-2.0（BRIA，最稳）。

常用参数：`background=Alpha`、`process_res=1024`、`mask_blur=1~5`、`mask_offset=-10~10`，开启 optimize / Refine Foreground。模型放 `models/RMBG/<模型>/`；多数模型首次使用自动下载。

## 动图与规格

> **本阶段非目标**（见 `docs/roadmap.md` §8）：下面这些只做记录，不要在本阶段实现。

生成端出多关键帧 → `ComfyUI-Frame-Interpolation` 插帧 → 合成端 ffmpeg / ImageMagick：

```bash
# GIF：调色板两遍法（Pillow 做不好）
ffmpeg -framerate 12 -i frame_%04d.png \
  -vf "scale=300:300:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" \
  -loop 0 out.gif

# 动态 WebP（ImageMagick 7 用 magick，不是 convert）
magick -delay 8 -loop 0 frame_*.png out.webp
```

**微信表情包规格**：单表情 **240×240**（静态 PNG / 动态 GIF），GIF 建议 ≤500 KB。300×300 是第三方渠道与 QQ 的常见值。其余位置：详情页横幅 750×400、封面 240×240、聊天面板图标 48×48、赞赏引导图 750×560。

## 下载通道与已知的坑

- `hf-mirror.com` 本机可达（HTTP 200），`huggingface.co` 直连也通。
- **坑（已实测）**：`hf-mirror` 的 `/api/models/...` 路径被代理拦截、返回空 → `huggingface-cli` 的"列出仓库文件"会失败。绕过方式只有两种：指定具体文件名的下载方式，或直接拼 `https://hf-mirror.com/<repo>/resolve/main/<path>`。
- `github.com` 可达；clone custom_nodes 超时时用 GitHub 代理前缀。
- ModelScope 同步 Qwen 系权重，可作备用源。

**权重来源（实际在用的地址）**：

| 文件 | 仓库 | 落点 |
|---|---|---|
| `flux-2-klein-4b-fp8.safetensors` | `black-forest-labs/FLUX.2-klein-4b-fp8` | `models/diffusion_models/` |
| `flux-2-klein-base-4b-fp8.safetensors` | `black-forest-labs/FLUX.2-klein-base-4b-fp8` | 同上 |
| `qwen_3_4b.safetensors` | `Comfy-Org/vae-text-encorder-for-flux-klein-4b`（`split_files/text_encoders/`） | `models/text_encoders/` |
| `flux2-vae.safetensors` | 同上（`split_files/vae/`） | `models/vae/` |

**格式：官方发的就是 fp8。** `-fp8` 不是我们自己量化的，是 black-forest-labs 发布的那一版（4.07 GB distilled / 4.09 GB base）；16 GB 显存下 fp8 够用，没有换 bf16 的理由。

**aria2c 多线程直连（实际用法）**：`huggingface.co` 直连可用，不需要镜像前缀。

```bash
aria2c -x16 -s16 -k1M -c --file-allocation=none \
  -d models/diffusion_models -o flux-2-klein-4b-fp8.safetensors \
  https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/main/flux-2-klein-4b-fp8.safetensors
```

`-c` 断点续传，`.aria2` 控制文件留在同目录。实测按 2 MiB/s 量级走——慢，但稳，中途断了能接着下。

## 未验证项（M1 必须回填）

1. Klein 4B 在本机的**实际出图速度与峰值显存**（纸面值不算数）。
2. Klein 工作流在 ComfyUI 里的**节点图长什么样**。本机 ComfyUI 实测 **0.42.0**（`GET /system_stats` 返回的 `comfyui_version`），`supported_models.py` 里已有 `Flux2` 类与 Klein tokenizer，理论上支持，但没跑过。
3. `denoise` 0.55 这个默认值是否合适。
4. ~~hf-mirror 下载 16.5 GB 的实际速度与断点续传表现~~ → 已实测，见上（走的是直连 + aria2c 断点续传）。
