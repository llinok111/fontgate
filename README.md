# fontgate

**把「字体子集化成功」变成「字体子集化可证明」。**

`pyftsubset` 告诉你文件写出来了。`fontgate` 告诉你它还能不能渲染你要的文字。

一个 Python 命令行工具，五项检查全部读自字体二进制本身：覆盖、空字形、度量、布局表、容器格式。任何一项不通过，退出码非零，CI 直接拦下。

```
fontgate 1.0.0 · 93 tests · MIT · Python ≥3.10 · 唯一运行时依赖 fonttools
```

---

## 1. 它解决什么问题

字体子集化是不可逆的破坏性操作：删字形、裁表、重排索引。工具报告成功，只说明文件写出来了。

以下三个失效模式都在本仓库的基线实验里复现过（数据见 §6）：

**① 缺字是静默的。** `pyftsubset` 的 `--ignore-missing-unicodes` 默认开启。文本里有 6 个字库里不存在的字符时，它退出码 0，删除这 6 个字符，什么也不说。加 `--no-ignore-missing-unicodes` 才报错，而报错形式是这样的：

```
fontTools.subset.Subsetter.MissingUnicodesSubsettingError: ['U+20BB7', 'U+1F389']
```

一串码位，没有字符本身，没有来源文件，没有出现次数。修的时候得自己反查。

**② 扩展名会撒谎。** 不带 `--flavor=woff2` 时，输出是裸 sfnt，即使文件名写成 `.woff2`。浏览器报 `OTS parsing error`，字体完全不加载。本仓库的基线实验就产出过这样一个文件：23,916 字节，magic bytes 是 `00 01 00 00`。

**③ 同一家族的粗体会丢字。** 微软雅黑 Regular 有 29,905 个码位，Bold 有 29,888 个。差的 17 个包括 `◌`（U+25CC）和片假名小写 `ㇰ`–`ㇹ`。这些字加粗时浏览器会回退到别的字体，同一句话里出现两种字形。

三条都不是"用错了"，是默认行为。所以需要一个独立的验收环节。

---

## 2. 怎么用

```bash
pip install fonttools brotli          # brotli 只在需要 woff2 输出时必需
pip install -e .                      # 或直接 python -m fontgate

fontgate inspect build/zh.woff2
fontgate audit   --font build/zh.woff2 --text src/
fontgate charset --text src/ --font build/zh.woff2 -o charset.txt
fontgate verify  --source zh.ttf --subset build/zh.woff2 --expect-file src/ --budget 120kb
fontgate family  --font zh-regular.woff2 --font zh-bold.woff2
```

### 一条完整的交付链路

```bash
# 1. 从真实内容里取字符，并与字体取交集（关键：交集保证可渲染）
fontgate charset --text src/ --font NotoSansSC-Regular.ttf -o charset.txt
#    stderr 会列出被丢弃的字符，例如：
#    6 characters dropped because the font cannot render them: ↵ U+21B5 ⚠ U+26A0 ...

# 2. 用上游工具子集化（fontgate 不重新实现子集化）
pyftsubset NotoSansSC-Regular.ttf \
  --text-file=charset.txt --flavor=woff2 --output-file=build/zh.woff2

# 3. 验收
fontgate verify --source NotoSansSC-Regular.ttf --subset build/zh.woff2 \
  --expect-file=charset.txt --budget 120kb --html build/report.html
```

第 3 步的退出码可以直接用在 CI 里：

```yaml
- run: fontgate verify --source zh.ttf --subset build/zh.woff2 --expect-file src/ --budget 120kb
```

### 五个命令

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `inspect` | 一个或多个字体 | 家族名、字重、轮廓类型、字形数、cmap 子表、可变轴、SHA-256 |
| `audit` | 字体 × 文本目录 | 每个字体缺哪些字、哪些字映射到空字形、来源文件与行号 |
| `charset` | 文本目录（可选 `--font`） | 纯文本 / `U+XXXX` 列表 / CSS `unicode-range` / JSON |
| `verify` | 源字体 + 子集字体 + 期望集 | 七项检查的逐项判定、体积比、JSON + 单文件 HTML |
| `family` | 多个字体 | 同族不同字重之间的覆盖差异与度量差异 |

`audit` 与 `verify` 都支持 `--json` 和 `--html`。HTML 是单文件，零外部请求，浅色/深色自适应。

---

## 3. 为什么不重复已有的工具

这个方向已经有成熟工具，所以差异化必须说清楚。核实日期 2026-10-24。

| 能力 | `pyftsubset` | `subfont` 7.3.0 | **fontgate** |
| --- | --- | --- | --- |
| 从静态页面抓字符 | ✗ | ✓（HTML/CSS，需 puppeteer） | ✓ 任意文本目录，多种格式 |
| 缺字警告 | ✗（静默丢弃） | ✓ | ✓ 带字符、码位、次数、文件、行号 |
| 执行子集化 | ✓ | ✓ | ✗（故意不做，调用上游） |
| 验收**任意工具**产出的子集 | ✗ | ✗ | ✓ |
| cmap 有映射但轮廓为空 | ✗ | ✗ | ✓ |
| 子集前后 advance / bbox 一致 | ✗ | ✗ | ✓ |
| 同族多字重覆盖一致性 | ✗ | ✗ | ✓ |
| 扩展名与实际容器不符 | ✗ | ✗ | ✓ |
| 布局特性丢失是否**真的影响目标字形** | ✗ | ✗ | ✓ |
| CI 退出码契约 | 部分 | ✗ | ✓ 五档 |
| 运行时依赖 | fontTools | 26 个 npm 包 | fontTools |

`subfont` 的 README 明确写了 "Warn you about usage of characters that don't exist as glyphs in your webfonts"，它确实做这件事。差别在于它是**生产工具**：输入是一个页面，输出是一套优化过的字体和改写过的 HTML。`fontgate` 是**验收工具**：输入是「源字体 + 某个工具产出的子集 + 你期望渲染的文本」，输出是一份可复核的判定。

两者可以串联：`subfont` 生成，`fontgate` 验收。

不做子集化本身是刻意的决定。重新实现一遍字形裁剪只会引入新的 bug，而验收的价值恰恰来自"不信任生产者"。

---

## 4. 检查项与判据

`verify` 的七项检查，每项独立给出 `pass` / `warn` / `fail` / `skip`。

| 检查 | 判据 | 失败意味着什么 |
| --- | --- | --- |
| `format` | magic bytes 与扩展名、`--declared-format` 三者一致 | 浏览器拒绝加载，字体完全不生效 |
| `tables` | 必需表齐全；源有而子集无的可选表记为警告 | 表缺失时排版引擎行为未定义 |
| `identity` | family 名与字重未变 | CSS 里的 `font-family` 找不到这个字体 |
| `coverage` | 期望集每个码位都在子集 cmap 中，且轮廓非空 | 页面出现豆腐块或静默回退 |
| `metrics` | 共同字符的 advance width 与 bbox 完全一致 | 一个字宽变化会让整行位移 |
| `layout` | GSUB/GPOS 引用不悬空；**影响到目标字形的**特性未丢失 | 连字断裂、变音符错位 |
| `budget` | 字节数不超过 `--budget` | 首屏预算被字体吃掉 |

两个判据值得单独说明，因为它们是"零误报"的关键：

**空字形不等于缺字。** cmap 里有 `U+6587 → uni6587`，但 `uni6587` 的 `numberOfContours` 是 0。排版引擎认为字符存在，渲染结果是一片空白。只查 cmap 的检查看不到这一层。`coverage` 会把它单独列为 `coverage-empty`。

**布局特性丢失要区分"裁剪"和"破坏"。** 一个只含中文的子集丢掉 `ccmp` 和 `mark` 是正确的——没有组合符号可用。所以判据不是"特性是否消失"，而是"源字体里这个特性的 Coverage 是否覆盖了我们实际发出的字形"。前者的误报率是 100%，后者为 0。

`audit` 另有两条规则：变体选择符（U+FE00–FE0F、U+E0100–E01EF）通过 cmap format 14 寻址，不计入缺字；`Zs`/`Cc` 类字符按需处理，控制字符不进入字符集。

---

## 5. 上游选型

三个候选，都做了源码与 issue 层面的核实，不是看 star 数。

| 候选 | 许可 | ★ | 核实结论 | 决定 |
| --- | --- | --- | --- | --- |
| [fonttools/fonttools](https://github.com/fonttools/fonttools) | MIT | 5,297 | `Lib/fontTools/subset/__init__.py` 4348 行，`Tests/subset/` 有独立测试目录与 100+ fixture；4.67.0 于 2026-10-14 发布；`--ignore-missing-unicodes` 默认 `True` 有源码依据（`__init__.py` 的选项默认值）；issue #953 仍在请求 CLI 便利参数 | **选它** |
| [svg/svgo](https://github.com/svg/svgo) | MIT | 22,766 | 已有 `test/regression/` 目录与渲染比对基础设施（issue #2249/#2313/#2246/#2254 都在围绕 regression rendering 迭代） | 淘汰：想做的"优化后像素回归"上游已经在做 |
| [OpenAPITools/openapi-diff](https://github.com/OpenAPITools/openapi-diff) | Apache-2.0 | 1,098 | 支持 OpenAPI 3.0 的参数/响应/端点逐层比较，输出 HTML/Markdown/Asciidoc/JSON；开放的非 PR issue 只有 1 条（#910） | 淘汰：功能完整，找不到可验证的缺口 |

### 为什么是 fonttools

它是这条链路上**唯一被所有工具共用、又只提供原子能力**的一层：`pyftsubset` 是它的 CLI，`subfont` 通过 harfbuzz 走另一条路，商业工具也读同样的表。它把"裁剪"做好，把"验证"留给使用者。

issue #953（`[subset] Add convenience arg for subtracting characters`）从 2021 年开到现在仍 open，说明上游对"CLI 便利性"这件事优先级不高。而缺字静默、扩展名撒谎、字重漂移这三件事，都不是上游的 bug——是它的设计边界。

许可证：MIT，允许商业使用、修改、再发布与再许可，只需保留版权声明。本项目的复用方式是**依赖其库，不复制源码**，见 `NOTICE`。

---

## 6. 实测数据

环境：Windows，Python 3.13.14，fontTools 4.67.0，brotli 1.2.0。
源字体：`msyh.ttc` face 0（Microsoft YaHei Regular），19,704,352 字节，SHA-256 前缀 `d79c55e6…`。
文本：`demo/site/` 三个文件（HTML / Markdown / CSS），361 个唯一码位，1,854 次出现。

### 子集化结果

| 指标 | 源 | 子集 | 比值 |
| --- | --- | --- | --- |
| 字节 | 19,704,352 | 107,164 | 0.544% |
| 字形 | 30,209 | 364 | 1.20% |
| cmap 码位 | 29,905 | 355 | 1.19% |
| 容器 | TTC | WOFF2 | — |

### 验收判定

```
PASS  format      woff2 container
WARN  tables      22 tables, 1 dropped from source     (kern)
PASS  identity    Microsoft YaHei 400
PASS  coverage    354/354 expected characters present
PASS  metrics     354 glyphs compared, 0 advance diffs, 0 bounds diffs
PASS  layout      24 glyph references, 1 features kept, 2 dropped (0 relevant)
PASS  budget      107164 / 409600 bytes
```

`coverage` 的分母是 354 而不是 361：361 里有 6 个字体本来就没有（`audit` 已单独报告），另有 1 个变体选择符不需要字形。这两个数字在 HTML 报告的 `Skipped` 列里可以对上账。

### 缺陷检测能力（可复现）

| 场景 | 构造方式 | fontgate 判定 |
| --- | --- | --- |
| 缺字 | 子集只给 `"AB"`，期望 `"ABC中文"` | `coverage-missing`，error |
| 空字形 | 源字体把 `U+4E2D` 画成 0 轮廓，再子集 | `coverage-empty`，error |
| 扩展名撒谎 | 复制 `.ttf` 到 `.woff2` | `format-mismatch`，error |
| 字宽篡改 | 子集后把 `U+0041` 的 advance 改 777 | `metrics-advance`，error |
| 家族改名 | 子集后改 name 表 family | `identity-drift`，error |
| 特性破坏 | `--layout-features=""` 且目标文本含 `A` | `layout-feature-lost`，error |
| 特性正常裁剪 | 同上，但目标文本只有中文 | 不报错（0 relevant） |
| 超预算 | `--budget 1b` | `budget-exceeded`，退出码 4 |

以上每一条都有对应单元测试，见 `tests/test_verify.py`。

### 多字重

```
Microsoft YaHei
  Regular   weight 400  cmap 29,905  digest a572f7c22907
  Bold      weight 700  cmap 29,888  digest 6ba31b9c90ab
WARN  Microsoft YaHei has 17 code points that not every weight covers
```

---

## 7. 退出码契约

| 码 | 含义 | 典型场景 |
| --- | --- | --- |
| 0 | 全部通过 | CI 继续 |
| 1 | 有检查失败 | 阻断构建 |
| 2 | 命令行用法错误 | 参数写错 |
| 3 | 输入不可读 | 文件不存在、不是字体、表目录截断 |
| 4 | 只有体积预算超限 | 便于单独处理"功能对但太大" |

`4` 单独一档是因为它和"字体坏了"是两类问题：前者可以拆分字体或放宽预算，后者必须修。

---

## 8. 已知限制

这些是实测确认的边界，不是谦虚话。

1. **只验收，不生产。** 不做子集化、不做 unicode-range 分片、不改写 HTML。上游工具负责这些。
2. **`layout` 检查是抽检，不是完整 OpenType 校验。** 它遍历 `mapping`/`alternates`/`ligatures` 与 `Coverage`，覆盖常见替换与定位子表；上下文替换（type 5/6）的嵌套规则不计入 `references_checked` 的分子，报告里会显示实际检查了多少条引用。
3. **`metrics` 只比 advance width 与 glyf bbox。** CFF 轮廓的 bbox 需要 `boundsPen`，当前未实现，此时只比 advance。
4. **不渲染。** 没有像素级比对，也不加载浏览器。`fontgate` 判定的是二进制层的一致性；"看起来对不对"仍需人工或独立截图。
5. **字符集来自静态文件。** 运行时由 JS 拼接、接口返回、用户生成的内容无法覆盖。这类场景应使用 `unicode-range` 分片 + 完整字库回退，而不是精确子集。
6. **未在 Linux/macOS 上跑过测试。** 测试用合成字体，理论上跨平台，但只在 Windows + Python 3.13 上实际执行过。CI 配置未包含在本仓库中。
7. **TTC 只按索引取面。** 不自动拆包，`audit` 会提示"浏览器不能直接加载 .ttc"。

---

## 9. 来源与许可

- 本项目：MIT，见 `LICENSE`。
- 依赖：[fontTools](https://github.com/fonttools/fonttools)（MIT），仅通过公开 Python API 使用，未修改、未 fork、未复制源码。声明见 `NOTICE`。
- WOFF2 读写需要 [Brotli](https://github.com/google/brotli)（MIT）的 Python 绑定，为可选依赖。
- 演示与测试**不包含任何第三方字体文件**。测试用 `tests/helpers.py` 里的 `fontBuilder` 现场生成合成字体；演示脚本读取操作系统已安装的字体，不随仓库分发。

如果你要再发布本项目的修改版，保留 `LICENSE` 与 `NOTICE` 即可，无需获得额外授权。

---

## 10. 开发

```bash
python -m unittest discover -s tests -v     # 93 tests
python demo/run_demo.py                     # 端到端：采集 → 子集 → 验收 → 报告
```

测试不依赖系统字体：`tests/helpers.py` 用 `fontBuilder` 生成只含指定码位的 TrueType 字体，可以精确构造空字形、篡改度量、注入布局特性等场景。

```
fontgate/
├── fontgate/
│   ├── charset.py    字符采集、编码探测、HTML 去标签、unicode-range
│   ├── fontfile.py   magic 检测、表目录校验、cmap 合并、空轮廓判定
│   ├── audit.py      内容 × 字体矩阵、家族一致性
│   ├── verify.py     七项检查、布局覆盖判定
│   ├── report.py     单文件 HTML
│   ├── models.py     finding 码表、canonical JSON
│   └── cli.py        子命令、退出码
├── tests/            93 个用例，全部合成字体
├── demo/             示例站点与端到端脚本
└── docs/             Pages 用的静态示例报告（由 docs/build_docs.py 重建）
```

在线示例报告：<https://llinok111.github.io/fontgate/>（数据来自 §6 的同一次基线实验，路径已改写为相对形式）。
