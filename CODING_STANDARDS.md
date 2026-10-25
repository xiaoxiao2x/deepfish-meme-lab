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

### 缩略图的一档宽只有一个来源

成品库的卡片与素材库的瀑布流是**同一档宽**，所以「缩到多宽、怎么缩」只有一份：
`lab/thumbs.py` 的 `WIDTH` 与 `to_png`。`showcase` 与 `pool` 各自保留一个
`thumb_bytes` / `THUMB_WIDTH` 的名字（那是它们对外的说法），实现全部转发过去。

这与上一条是同一类：不是洁癖，是「两处各写一份 480」的漂不会被任何门禁抓到 ——
`scripts/check_docs.py` 比的是键集，比不出两个常量相不相等。改了一处、另一处
悄悄不对，页面只会看着别扭，不会报错。

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
| `entry_id` 之外还有几处 `str()` 兜底 | 默认值散落 | 读账时缺字段按默认值走（`seed` 缺了是 0），这是账本向后兼容的代价，不是疏漏。硬要在 `from_json` 一处做，就得给每个字段声明默认值，等于抄一遍 `Entry` 的定义。同一种账：[server] 缺段时 `lab/config.py` 里那两个默认值（`127.0.0.1`、`8765`）与 `config/default.toml` 是同一份东西的第二处 —— **留着是因为八个测试 fixture 的 TOML 里都没有 `[server]`**，改用 `_require` 会全红。再加第三处之前先回来看这一条。 |
| `lab/pool.py` 的 `_was_used` / `_used` 两个私有函数，名字都带「用过」 | Mysterious Name，该合成一个 | 两个回答的是两个问题：`_used` 问「账上都有什么」（翻一遍两本账，贵），`_was_used` 问「这一张算不算」（纯比较，便宜）。瀑布流一次摆几百格，先把账翻一遍再逐格比；合成一个就得每格重翻一遍账。函数名在调用点旁边（`tiles` / `find_tile`），读起来不费解。 |
| `lab/static/app.js` 的 `useBase(relative, label, preview)` 三个裸字符串 | Primitive Obsession，该合成一个对象 | 三样都是「底图这一栏要显示什么」：值进表单、名字进 `<p>`、地址进 `<img src>`。从素材库送来时三样都有，从成品库重跑时至少值必有 —— 合成对象只是把三次赋值挪进一个括号，读调用点的人还要多跳一次。真要改，该改的是「底图有两种来路」这件事本身，别从参数形状上下手。 |
| `lab/web.py` 的 `asset_thumb` / `asset_file` **每个请求重读一遍索引** | 性能（415 张 = 415 次读盘） | 索引不进仓库、重扫一次就换一本，缓存要跟着失效，判失效比读一遍贵。实测一页 415 格 0.05 秒、单张缩略图 0.07 秒（含现缩），够快；真慢了先量再改，别按想象加缓存。命中率高的缓存是另一个话题（`functools.lru_cache` 挂在 `open_index` 上会与"重扫即换"打架）。 |
| tests 没有 `conftest.py`，`_cfg` 每个测试文件各一份 | 测试公共设施缺失 | 它们钉的不是同一件事：`test_naming` 只挪 `output_dir`、`test_keeping` 连 `repo_root` 一起挪、`test_jobs` 还要把 `workflows/` 拷进 tmp、`test_lettering` 顺手换掉字体、`test_postprocess` 让调用方指定输出目录、`test_web` 干脆读真仓库。合成一份就得带六个可选开关，每加一票都牵动全部文件。**这条不再是「等以后合」，是已定的现状**：新文件照抄最接近的那一份，别为复用去改别人的 fixture。 |

**这条要反过来守**：新写测试**不要**再用 `Entry(**{**base.__dict__, **overrides})` 造变体 ——
那是绕开 frozen dataclass 改字段，字段改名时会静默出错。用 `dataclasses.replace(base, seed=1)`。
（现有那几处是历史，改到的时候顺手改，不专门开票。）

## 三、改代码时怎么用这份文件

- **`/code-review` 的 Standards 轴**：先按这一份判。命中第一节的直接算硬违规；命中第二节的，
  只在真的有新理由时才提，并在报告里说明「CODING_STANDARDS.md 第二节说了不改，但…」。
- **`/implement` 收尾**：`./scripts/check.sh` 六条全绿是底线，不是全部。这份文件里的规矩
  门禁抓不到，得人看。
- **新增 `lab/*.py`**：`scripts/check_docs.py` 会要求你进 `docs/design.md` §4 的架构树，或者在
  它 `OFF_TREE` 那张表里写一句「为什么它不是一层」—— 两个都行，但必须做一次决定。
- **改 `Entry` 的字段**：`docs/design.md` §6.2 的键集与 `Entry.as_json()` 由门禁对齐，两处一起改。
- **动模块的 `__all__`（加、删、改名）**：`./scripts/check.sh contracts` 对账三件事 —— 清单里列的名字
  模块里真拿得出、`lab/` 内部 `from .某模块 import 某名字` 的那个名字在对方清单里、`tests/` 之间没
  互相借带下划线的私有名。以前 `lab/pipeline.py` 从 `workflows` 借 `model_name` 借了很久，清单里
  根本没有它，改一次 `workflows` 的对外说法就会静默崩在两个下游模块。**完成判据**：contracts 打印
  「契约对得上」，且这次 diff 里 `__all__` 与真实定义同时改了。
- **改完一处名字，先 `./scripts/check.sh ruff mypy`**。这两条加起来约 0.1 秒（实测 ruff 0.007、
  mypy 0.10），比全量测试便宜两个数量级。`showcase.picture_at` 改名漏改一处调用点，`lab/web.py`
  的缩略图路由撞了十六次 `AttributeError` 才被发现 —— 单跑这两条当场就红。挪函数、改签名、改常量名
  之后同样先跑它。**完成判据**：两条门禁都打印「门禁过了」，且 `grep` 旧名字在仓库里零命中。
- **凡碰账本或文件路径的 fixture，至少有一条测试真走 `/api/*` 端到端**。手造的条目用来钉分支，
  端到端那条用来钉「真实流程写出来的账长什么样」：素材库 `?unused=1` 恒不过滤那次，根因正是三个
  fixture 手造 `EntrySource(image=<素材原路径>)`，而真实链路写的是数据夹里的副本名 —— 静态检查与
  手造数据都看不出来，两边各自自洽。**完成判据**：这票的测试文件里指得出一条真实请求（`post_json`
  打 `/api/…` 或等价），它读的账由本进程跑出来，不是 fixture 拼的。

## 四、工具链的下界

- Python `>= 3.11`（`pyproject.toml` 的 `requires-python`，ruff 的 `target-version` 与 mypy 的
  `python_version` 都钉在 3.11）。写 3.12 才有的语法，本地 3.14 上跑得动，CI 上会炸。
- 行宽 100，`ruff` 的 select 是 `E4 E7 E9 E501 F C4 UP I B W`。**规则默认值随 ruff 版本漂**，
  所以版本钉在 CI 与 README 里，改的时候两处一起改。行宽与末尾换行都在这份 select 里 ——
  写进标准的规矩得有一条工具在读，否则「行宽 100」只是一句话（`E501` 与 `W` 是后来补的，
  补之前 16 处超长行、6 处缺末尾换行躺了整整一轮）。
- `mypy` 查 `lab` **和 `tests`**（`pyproject.toml` 的 `[tool.mypy] files`）。测试里的假引擎
  是手写的替身，签名漂了只有类型检查拦得住；`scripts/check.sh` 调 `mypy` 时不带目录参数，
  查哪些文件由 `pyproject.toml` 一个地方说了算。`ruff` 也一样不带路径（不带 = 扫整个仓库，
  `scripts/` 在内）—— 写死 `check lab tests` 的那几年，`scripts/check_docs.py` 自己就没人查。
- `check_untyped_defs = true` 是**必须**开着的（`pyproject.toml` 的 `[tool.mypy]`）。默认
  mypy 对没写返回类型的函数只看签名、跳过函数体 —— 而测试函数几乎都不写 `-> None`，等于
  tests 全部只被「能不能 import」检一遍，报告里却写着 "no issues found"。开了之后要付出的
  代价只是：Pillow 那几个返回 Optional 的 API（`getpixel` / `load` / `getcolors`）在测试里
  得用 `cast` 或 `assert ... is not None` 收窄。**别为了让某个文件闭嘴去关它**。
- 浏览器那侧**没有构建步骤**（design.md §5「不引入 Node 构建链」、§9.1「零构建步骤」），
  也没有任何测试会执行 `lab/static/*.js`。所以门禁里单有一条 `js`：`node --check` 把三个
  脚本各解析一遍。node 是开发机上的工具、不是项目依赖 —— 没装的机器上 `./scripts/check.sh js`
  打印一行跳过、不算红。**`node --check` 只解析不执行，不碰 DOM 也不联网**，它补的是「语法
  都写错」这一档，不替代人对页面行为的检查。
- 三方依赖只有这几样：`Pillow`（读图与后处理），`FastAPI` / `uvicorn` / `Jinja2`（本地界面），`pydantic`（表单类型，随 FastAPI 一起来，仍然在 pyproject 里写明）。
  界面那一套只在 `lab/web.py` 与 `lab/cli.py` 的 `_run_serve` 里 import —— `lab doctor` 与出片
  链路仍然是标准库 + Pillow，这条不能破。往 `lab/` 里再加依赖之前先想：这条能不能用标准库解决
  （`lab/naming.py`、`lab/paths.py`、`lab/registry.py` 都是零依赖的，是有意的 —— 它们被每一层 import）。
