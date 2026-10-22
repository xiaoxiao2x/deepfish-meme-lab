# AGENTS.md

鱼片加工厂（deepfish-meme-lab）：一个只为自己用的**本地工作台**，生成 DeepSeek 鲸鱼娘与蓝色大肥鱼的梗图、表情包。

开始动手前先读这两份定稿文档：

| 文件 | 回答的问题 |
|---|---|
| [docs/design.md](docs/design.md) | 是什么、不做什么、架构、技术选型、数据格式、GUI 大致设计 |
| [docs/roadmap.md](docs/roadmap.md) | 先做什么、做到什么程度算成、已知的坑 |

硬约束（详见 design.md §3.2「非目标」）：不做网站、不训 LoRA、不做多用户、不接在线生图 API。素材（803 张，其中 415 张挂在素材池）与生成成品不进仓库，仓库只留 `workflows/` 与 `data/registry.json`。

## Agent skills

### Issue tracker

Issues live in this repo's GitHub Issues (`xiaoxiao2x/deepfish-meme-lab`), accessed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary, each label string equal to its role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` at the repo root for domain vocabulary, plus `docs/adr/` for hard-to-reverse decisions. See `docs/agents/domain.md`.
