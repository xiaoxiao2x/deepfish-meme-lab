# AGENTS.md

鱼片加工厂（deepfish-meme-lab）：一个只为自己用的**本地工作台**，生成 DeepSeek 鲸鱼娘与蓝色大肥鱼的梗图、表情包。

开始动手前先读这两份定稿文档：

| 文件 | 回答的问题 |
|---|---|
| [docs/design.md](docs/design.md) | 是什么、不做什么、架构、技术选型、数据格式、GUI 大致设计 |
| [docs/roadmap.md](docs/roadmap.md) | 先做什么、做到什么程度算成、已知的坑 |

硬约束（详见 design.md §3.2「非目标」）：不做网站、不训 LoRA、不做多用户、不接在线生图 API。素材（两个来源共 803 张，入池 415 张）与生成成品不进仓库，仓库只留 `workflows/` 与 `data/registry.json`。

## 改完代码

跑 `./scripts/check.sh` —— pytest + ruff + mypy + docs（文档与代码对账），四条全过才算过（规则集与收紧项固定在 pyproject.toml 的 `[tool.ruff]` / `[tool.mypy]`，不要让它随工具版本漂）。克隆后装一次提交前钩子：`git config core.hooksPath scripts/git-hooks`（某次想跳过：`SKIP_CHECKS=1 git commit`）。单跑一个测试文件用 `.venv/bin/python -m pytest tests/test_x.py` —— 系统 `python` 没装 pytest，`check.sh` 自己会挑 venv。

要出图得先有引擎，工作台不管它的生命周期（ADR-0004）：`./scripts/comfy.sh start|stop|status|log`。

要比好几张图时，先拼成一张再看（`read_image` 每张都进上下文，十来张就是三万 token）。

门禁守得住静态检查与「文档与代码对不上」，守不住品味。**改代码、跑 `/code-review` 之前先读 [CODING_STANDARDS.md](CODING_STANDARDS.md)** —— 里面是已经定过的写法（号只有一个来源、借参数只认 orig、表格宽度只有一个来源……）和**明知不改的那几条**（Primitive Obsession、Data Clumps、`"未知"` 三处），再看到不必重新论证。

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`xiaoxiao2x/deepfish-meme-lab`): `deck_*` tools to claim / patch / report, `gh` CLI when the full body or the comments matter. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` at the repo root for domain vocabulary, plus `docs/adr/` for hard-to-reverse decisions. See `docs/agents/domain.md`.
