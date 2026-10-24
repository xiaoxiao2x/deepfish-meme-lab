# CODING_STANDARDS.md

这份文件回答一件事：**这个仓库里已经定下来的写法是什么，以及为什么。**

给谁看：接手改代码的 agent、跑 `/code-review` 的两条轴（Standards 与 Spec）。
`scripts/check_docs.py` 守得住「文档与代码对不上」这一类（标题集合、§6.2 的键、架构树、术语表禁用词），
守不住品味类判断。下面这些条目是三轮 review 反复提过、逐条判断过之后留下来的结论 ——
**照它写，就不用再重新论证一遍；想改它，先改这份文件再改代码。**

## 一、已经定了的写法

### 号只有一个来源

`lab/naming.py` 的 `entry_id(serial)` 是「`fish-007`」这个字符串的唯一定义处。三站的
`MadeImage.id` / `MadeSticker.id` / `MadeLettering.id` 都 `return entry_id(self.serial)`。

新代码要拼号就调它，不要写 `f"fish-{serial:03d}"`。理由不是洁癖：号一旦有两种拼法，
改格式（补零位数、加前缀）时漏掉一处，账里的号就对不上图上的号，而账的全部价值就是能对回去。

### 落点与编号的判据只住在 `plan_output`

「这个号能不能用」有三个分支（名字里没有号 / 号被当天的账占着 / 落点目录里已有同号文件），
全在 `lab/naming.py` 的 `plan_output` 里，靠 `known=` 与 `taken` 两个入参调。调用方只负责
把「当天的账读过哪些号」递进去，不自己加条件。

这条是踩出来的：去背站原先自己判一遍，结果外来图沿用了本子上已有的号，贴纸借到了完全
无关那一条的 prompt 和种子。一个判断散在两个地方，迟早对不上。

### 记录层只借 `orig`，借不到就不借

`lab/recording.py` 的 `find_base` 只认同号的 `orig`。同号只剩贴纸时（原图被清走），
去背与加字宁可不借 —— 借贴纸那条会把 `workflows/fish-sticker.json` 和去背模型的文件名
当成这张图的配方，写进配方库就是一条假的来路。

`_borrow_version` 同一条规矩的另一半：这一步真问不到引擎版本才往上一条借，借不到才写
「未知」。「未知」盖掉上一步真记下的版本，等于把复现最需要的那一栏抹了。

**这两处是判断，不是搬运** —— 认识这一点很重要：`lab/recording.py` 的文件头写着「翻译 +
借上一步的两条规则」，这两条规则必须住在这里。放到调用方手里，三个站会各写一套借法。

### `kind` 只认三种，其余一律不认

`lab/keeping.py` 的 `kind_of` 要过两道条件：号是 `fish-###` 那个形状，**且**中间那段在
`KNOWN_KINDS`（`orig` / `sticker` / `captioned`）里。这不是 `stage_of` 的转发 —— 拆名字的
规则对任何三段以上的文件名都给出非空答案，而外来的图也叫得出中间那段
（`screenshot.2026.10.24.png` 的 `24`、`cover.final.png` 的 `final`）。照拆名规则一律当真，
keep 一张素材图就会往 schema 里写一个没人认识的阶段。

### 两本账的异常契约：只抛 `ValueError`

`lab/registry.py` 的 `load_entries` 承诺「文件读不出来就抛 `ValueError`，且带上路径」。
这是给 CLI 用的：`_run_keep` / `_run_gallery` 只接 `ValueError`，别的一律变成 traceback。
所以：

- 新增读账代码不许让 `KeyError` / `TypeError` / `AttributeError` 漏出去；
- 值为 `null` 与缺键同罪（`str(None)` 会洗成字面 `'None'`，接着被写回账里）；
- `items` 不在一律当空账（`data.get("items") or []` 会把 `""`、`0`、`false`、`null` 静默
  当成空账，而 `write_entry` 是整本读-改-写，等于抹掉一整天）。

### 表格宽度只有一个来源

`lab/cli.py` 的 `GALLERY_COLUMNS` 是列宽的唯一来源；表头、数据行、横线都从它算
（`_gallery_width()` / `_gallery_row()`）。加一列就改这一处，别在别处写死数字 ——
写死的那个数字迟早和列宽分家，分家之后表格就歪了，而表格歪了没人会报错，只会看着难受。

### 中文文案的形状

报错与提示是给未来的自己看的，要能指导下一步：

- 一句「哪里不对」，跟一句「怎么办」，中间用 `——` 连（`引擎连不上：127.0.0.1:8188 ——
  先 ./scripts/comfy.sh start 把引擎起起来再重来。`）；
- 术语照 `GLOSSARY.md` 的定义写，不用它列的 Avoid 词（`出图`、`产物`、`登记册`、`文案` …）。
  行内加 `# glossary-ok:` 可以豁免整行，但**先想清楚是不是词汇选错了**，豁免是最后一招；
- 数字、路径、命令原样保留，中文只在它们周围。

## 二、明知不改的（review 会再报一遍，这里一次讲清楚）

下面几条都被提过，判断的结果是**保留现状**。再看到时直接引用这一节，不必重新讨论。

| 位置 | 报的是什么 | 为什么不改 |
|---|---|---|
| `lab/registry.py` `write_entry(..., library: bool = False)` | Primitive Obsession，该换成枚举 | 两本账形状完全相同，只有抬头那一句不同。为两个取值引入枚举，读代码时要多跳一次才知道 `library=True` 是什么意思。一个 bool 恰好说完了这件事。 |
| `entry_for_gen` / `entry_from_base` 六个同名关键字 | Data Clumps，该抽参数对象 | 同名不等于同一件事：`spec` 那一串只有出片站有，去背与加字是从上一条借的。真正共用的结构是 `Entry` 本身，已经在共用。抽一个中间对象只会多一层搬运。 |
| `"未知"` 三处（`recording.UNKNOWN_VERSION`、`doctor.UNKNOWN_VERSION`、`comfy.version()` 里的字面量） | 重复常量 | 三处是三个依赖方向：`comfy.py` 是最底层，不 import 上层（方向反了会成环）；`doctor.py` 是只读体检，不该为一句文案拖进记账层。**新增第四处之前先看看能不能复用** —— 这条留的是余地，不是许可证。 |
| `entry_id` 之外还有几处 `str()` 兜底 | 默认值散落 | 读账时缺字段按默认值走（`seed` 缺了是 0），这是账本向后兼容的代价，不是疏漏。硬要在 `from_json` 一处做，就得给每个字段声明默认值，等于抄一遍 `Entry` 的定义。 |
| tests 没有 `conftest.py`，`_cfg` 在四个文件各一份 | 测试公共设施缺失 | 那几份 fixture 会随票改形状（这一版加 `requested_*`，下一版可能加别的），合并成一个共用版本会让每次改动都牵动全部文件。等到第三份完全一样、且连续两票都没动过，再合。 |

**这条要反过来守**：新写测试**不要**再用 `Entry(**{**base.__dict__, **overrides})` 造变体 ——
那是绕开 frozen dataclass 改字段，字段改名时会静默出错。用 `dataclasses.replace(base, seed=1)`。
（现有那几处是历史，改到的时候顺手改，不专门开票。）

## 三、改代码时怎么用这份文件

- **`/code-review` 的 Standards 轴**：先按这一份判。命中第一节的直接算硬违规；命中第二节的，
  只在真的有新理由时才提，并在报告里说明「CODING_STANDARDS.md 第二节说了不改，但…」。
- **`/implement` 收尾**：`./scripts/check.sh` 四条全绿是底线，不是全部。这份文件里的规矩
  门禁抓不到，得人看。
- **新增 `lab/*.py`**：`scripts/check_docs.py` 会要求你进 `docs/design.md` §4 的架构树，或者在
  它 `OFF_TREE` 那张表里写一句「为什么它不是一层」—— 两个都行，但必须做一次决定。
- **改 `Entry` 的字段**：`docs/design.md` §6.2 的键集与 `Entry.as_json()` 由门禁对齐，两处一起改。

## 四、工具链的下界

- Python `>= 3.11`（`pyproject.toml` 的 `requires-python`，ruff 的 `target-version` 与 mypy 的
  `python_version` 都钉在 3.11）。写 3.12 才有的语法，本地 3.14 上跑得动，CI 上会炸。
- 行宽 100，`ruff` 的 select 只有 `E4 E7 E9 F C4 UP I B`。**规则默认值随 ruff 版本漂**，所以
  版本钉在 CI 与 README 里，改的时候两处一起改。
- 三方依赖只有 `Pillow`。往 `lab/` 里加依赖之前先想：这条能不能用标准库解决（`lab/naming.py`、
  `lab/paths.py`、`lab/registry.py` 都是零依赖的，是有意的 —— 它们被每一层 import）。
