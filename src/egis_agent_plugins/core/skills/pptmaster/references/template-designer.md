> 通用技术约束见 shared-standards.md。

# Template Designer — 模板设计角色

## 核心使命

基于已确定的模板简报，为**全局模板库**生成可复用的页面模板。

> 这是一个独立角色：仅通过 `/create-template` 工作流触发。它**不是**主 PPT 生成流水线中的项目级模板选择/定制步骤。

## 使用方式

- **触发**：`/create-template` 工作流
- **输出位置**：`templates/layouts/<template_name>/`
- **输入**：已确定的模板简报（模板 ID、显示名称、类别、适用场景、调性、主题模式、画布格式、可选参考素材）

当工作流提供 PPTX 参考源时，有效输入包来自统一的 `pptx_template_import.py` 准备工作区，变为：

- 已确定的模板简报
- `manifest.json` — 唯一事实来源（页面尺寸、主题、逐母版主题、素材、素材映射、占位符、版式、母版、幻灯片、SVG 文件路径、页面类型候选）
- `summary.md` — 从 manifest.json 派生的简短导引摘要
- 导出的 `assets/`
- `svg/master_*.svg` / `svg/layout_*.svg` — 演示文稿中每个母版/版式渲染为独立 SVG，包括未被示例幻灯片引用的（模板包通常附带比示例实际使用更多的设计面）
- `svg/slide_NN.svg` — 每页自有形状和页面级背景；母版/版式装饰和背景**未**内联于此
- `svg/inheritance.json` — 每页消费的版式/母版对应关系
- `svg-flat/slide_NN.svg` — 配套视图；每页自包含，可单独预览或截图而不丢失周围装饰。作为"PowerPoint 实际显示效果"的核查参考，而非创作来源——母版/版式装饰在每个 flat 页面中重复。
- 可选截图用于视觉交叉验证

PPTX 导入解读：

- 母版/版式 SVG 中的占位符参考线是布局信号。使用 `manifest.json` 占位符记录获取类型/索引/几何/基础样式；除非视觉设计确实使用虚线框，否则不要将虚线参考线框复制到最终模板。
- 图表、SmartArt、示意图和 OLE 对象可能在分层 SVG 中显示为类型化占位符。在 flat SVG 中可能显示预览图片。将其视为源意图标记，而非可复用的装饰素材。
- SVG 引用的素材文件名受 manifest 素材映射管控。优先使用这些引用，不要自造重复文件名。

PPTX 支持的模板创建输入优先级：

1. `manifest.json` 获取所有事实元数据（主题、素材、独特版式/母版结构、幻灯片复用、页面类型指引）
2. `svg/master_*.svg` + `svg/layout_*.svg` — **演示文稿共享视觉语言的主要来源**：背景、页面装饰条、重复品牌图案。新模板的固定结构应采纳或重新演绎这些元素。在阅读任何 slide SVG 之前先阅读这些。
3. `svg/inheritance.json` 确认哪个 slide 使用哪个 layout / master
4. 导出的 `assets/` 获取可复用视觉资源
5. `svg/slide_NN.svg` — 每页独特内容，用于判断排版节奏和内容密度（非固定结构用途）
6. `summary.md` 仅作快速浏览；绝非权威事实来源
7. 截图 / 原始 PPTX 仅用于风格验证

---

## 页面名册

输出页面集由**复制模式**决定，在已确定的模板简报中声明：

| 模式               | 适用场景                                                                                     | 名册                                                               |
| ------------------ | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `standard`（默认） | 大多数模板——简洁、可复用、覆盖均衡                                                           | `01_cover`, `02_chapter`, `03_content`, `04_ending`, 可选 `02_toc` |
| `fidelity`         | 用户明确要求严格复制源 PPTX，但仍希望 AI 清理/聚类/封顶变体                                  | 标准名册 + 每个 `manifest.json` 中发现的独特版式聚类一个变体       |
| `mirror`           | 用户希望逐页保留源幻灯片作为参考页（他人精美演示文稿用作库模板）。逐字复制，不抽象，无占位符 | 每源页一个 SVG，按源顺序命名 `<NNN>_<page_type>.svg`               |

### Standard 模式

| #   | 文件名           | 用途   | 说明                                            |
| --- | ---------------- | ------ | ----------------------------------------------- |
| 01  | `01_cover.svg`   | 封面   | 固定结构：标题、副标题、日期、组织              |
| 02  | `02_chapter.svg` | 章节页 | 固定结构：章节编号、章节标题                    |
| 03  | `03_content.svg` | 内容页 | 灵活结构：仅定义页眉/页脚；内容区由 AI 自由排版 |
| 04  | `04_ending.svg`  | 结束页 | 固定结构：感谢语、联系信息                      |
| --  | `02_toc.svg`     | 目录页 | 可选：目录标题、章节列表（编号+标题）           |

**设计哲学**：模板定义视觉一致性和结构页面；内容页保持最大灵活性。

**命名说明**：目录页保持 `02_toc.svg` 命名以兼容模板库和排序。

### Fidelity 模式

当简报设置 `Replication mode: fidelity` 时，从 `manifest.json` 页面类型聚类派生页面名册，每个独特视觉聚类输出一个 SVG。

**变体命名**：在父类型索引后追加小写字母后缀，保持排序：

| 父类型  | 变体示例                                                                        |
| ------- | ------------------------------------------------------------------------------- |
| Chapter | `02a_chapter_full.svg`, `02b_chapter_minimal.svg`                               |
| Content | `03a_content_two_col.svg`, `03b_content_data_card.svg`, `03c_content_quote.svg` |
| Ending  | `04a_ending_thanks.svg`, `04b_ending_contact.svg`                               |

标准四类之外的扩展页面类型（过渡/附录/免责/分隔）取下一空闲索引：`05_section_break.svg`, `06_appendix.svg`, `07_disclaimer.svg`。

**名册决策**：

- 按 `pageType` + 视觉结构（列数、主视觉 vs. 图标网格 vs. 引语等）从 `manifest.json` 聚类幻灯片
- 每聚类一个 SVG — **不要**为仅由单页代表的聚类输出变体，除非该页面结构确实不同于现有变体
- 每个视觉上不同的聚类一个变体——让源的结构多样性驱动数量。仅合并**近似重复**（相同列数、相同主元素、相同内容密度）；不要为减少变体数而合并真正的结构差异。如果发现自己想对每个源页面出一个变体，这说明用户应使用 `mirror` 模式而非 `fidelity`
- 在 `design_spec.md §V 页面名册` 和 `layouts_index.json` 条目的 `pages` 字段中记录每个输出页面（由 `register_template.py` 自动收集）

> 变体复用父类型的占位符集——见下方 §4（占位符参考）。

### Mirror 模式

当简报设置 `Replication mode: mirror` 时，角色**不做**抽象或重构。每个源页面成为一个**逐字节**的模板页面：

- 来源：`<import_workspace>/svg-flat/slide_NN.svg`（自包含的"PowerPoint 实际显示"视图）。**不要**读取或使用 `svg/master_*.svg`、`svg/layout_*.svg` 或 `svg/inheritance.json` —— 装饰/内容分离与 mirror 无关，因为 mirror 不插入占位符。
- 输出：`templates/layouts/<template_id>/<NNN>_<page_type>.svg`，其中 `<NNN>` 是零填充的源页面索引（3 位数），`<page_type>` 来自 `manifest.json` `pageTypeCandidates` — `cover` / `toc` / `chapter` / `content` / `ending`。页面类型启发式不明确时回退为 `content`。通过数字前缀保持源幻灯片顺序。
- 允许的修改：**仅**重写 `<image href="...">` 路径指向本地 `assets/` 副本，以及将素材文件重命名为语义化名称。其他一切——几何、装饰、sprite-sheet 包裹、原始示例文本、图表占位符、嵌入字体——原样复制。
- 禁止的修改：插入 `{{TITLE}}` / `{{CONTENT_AREA}}` / 任何其他占位符；"清理"装饰复杂度；合并相似页面；去除母版/版式装饰（它已烘焙在 flat SVG 中，保留原样）。
- `design_spec.md` §V 页面名册列出每个输出文件并附**一行描述**说明该页包含什么及适合什么内容槽位——Strategist 纯粹从这些描述选择 mirror 页面，因为 SVG 本身无占位符契约。

**Mirror 无占位符的原因**：它作为**视觉参考**而非模板表单被消费。Executor 的 mirror 路径（见 [executor-base.md](executor-base.md) §1.1）将选定的 mirror 页面复制到项目中，并根据项目内容就地编辑文本元素——不发生 `{{}}` 替换。这使库资产 100% 保持原样，用户可直接浏览 `templates/layouts/<template_id>/` 重新发现源演示。

**Mirror 不是什么**：像素级精确的重渲染管线测试。图表、SmartArt、OLE 对象及 EMF / WMF 媒体在 `pptx_template_import.py` 中无法往返的，在 mirror 中同样会失败。如果导入工作区有缺失媒体或不支持的对象，mirror 继承这些缺口——应在生成开始前告知用户。

---

## 模板设计规范

### 1. 必须生成 design_spec.md

**范围规则——仅限个性部分。** 模板 `design_spec.md` 描述**这个模板的识别特征**：品牌色彩、标志性装饰图案、逐页视觉特征、附带素材。它**不**重述通用约束——这些在规范参考中，所有下游角色已加载：

- SVG 技术约束、PPT 兼容性规则 → [`shared-standards.md`](shared-standards.md)
- 通用布局模式库、间距段位、字号比例段位 → [`templates/design_spec_reference.md`](../templates/design_spec_reference.md)（Strategist 编写**项目** design_spec 时读取）
- 标准占位符词汇表 → 下方 §4
- 内容方法论（金字塔 / SCQA / MECE） → [`strategist.md`](strategist.md)

在模板 `design_spec.md` 中重复声明这些都是噪音——Strategist 上下文中已有它们，重复会导致每次放宽都要扫描 N 个模板而非一处。**如果规则是通用的，省略它。如果本模板打破了通用规则，仅写偏差。**

**必需骨架：**

```markdown
---
template_id: <id>
category: brand | general | scenario | government | special
summary: <一行调性与用途>
keywords: [tag1, tag2, tag3]
primary_color: "#......"
canvas_format: ppt169
replication_mode: standard | fidelity | mirror
# 可选 — 仅当本模板覆盖标准占位符词汇表时。
# `mirror` 模式完全省略（mirror 无占位符）。
# placeholders:
#   01_cover: ["{{TITLE}}", "{{SUBTITLE}}", "{{BRAND_LOGO}}"]
#   03_content: ["{{KEY_MESSAGE}}", "{{CONTENT_AREA}}"]
---

# [模板名称] — 设计规范

## I. 模板概述
- 适用场景、设计调性、主题模式（浅色 / 深色 / 混合）
- 一段话：一眼能识别本模板的视觉特征

## II. 色彩方案
- HEX 值带角色标签（主色 / 强调色 / 背景色 / 文字色等）
- 品牌专属应用规则（如有，例如"KPI 卡片按蓝→绿→红→黄轮换"）

## III. 字体排版（使用默认 `Arial, "Microsoft YaHei", sans-serif` 字体栈时省略）
- 仅当模板有意偏离时列出各角色字体栈（展示衬线标题、品牌字体等）
- 非预装字体领头时的安装或嵌入需求
- 正文基线 px（参考信息；`spec_lock.md` 掌管各项目实际值）

## IV. 标志性设计元素
- 构成本模板身份的装饰图案——顶部色条、渐变下划线、logo 处理、品牌徽章位置
- 可选的 XML 片段（本模板独有的可复用组件）

## V. 页面名册
每输出 SVG 一行，描述本模板版本的封面/章节/内容/结束页外观（背景处理、装饰锚点、布局节奏）。`fidelity` 模式还应注明聚类来源和视觉区分点。`mirror` 模式下名册是**承重工件**——Strategist 纯粹从这些描述选页，因此每行必须包含足够细节以区分同类（列数、主元素、内容密度、适合什么内容槽位——如"三列 KPI 网格大数字，适合季度总结"）。名册条目须与磁盘上的实际 SVG 文件匹配。

## VI. 素材（无时省略）
随模板包附带的 logo、封面背景、品牌纹理——文件名、尺寸、预期用途。

## VII. 占位符覆盖（无时省略）
引用 `placeholders:` frontmatter 声明并解释理由（如"咨询类演示以 `{{KEY_MESSAGE}}` 代替 `{{PAGE_TITLE}}` 领头"）。
```

模板 `design_spec.md` 中应**省略**的章节（已有其他来源——列在此处是噪音）：

| 不要写                                             | 来源                           |
| -------------------------------------------------- | ------------------------------ |
| SVG 技术约束 / 必须规则 / 禁止元素                 | `shared-standards.md` §1       |
| PPT 兼容性规则（`<g opacity>`、仅行内样式等）      | `shared-standards.md`          |
| 通用布局模式库（居中卡片 / 三栏 / 时间线 / …）     | `design_spec_reference.md` §V  |
| 通用间距段位（边距 40-60px、卡片间距 20-32px 等）  | `design_spec_reference.md` §V  |
| 通用字号层级（封面 2.5-5x 正文、页面标题 1.5-2x…） | `design_spec_reference.md` §IV |
| 标准占位符表（`{{TITLE}}`、`{{PAGE_NUM}}`…）       | 下方 §4                        |
| 内容方法论（金字塔 / SCQA / MECE）                 | `strategist.md`                |
| "使用说明"样板（复制模板 / 选择页面 / …）          | `create-template.md`           |
| 创建日期 / 页数行                                  | 非库级字段                     |

当改写已含上述省略章节的现有模板时，直接删除它们——不要留下"见 XXX"指针。这条范围规则就是该指针的替代。

### 2. 继承设计规范

模板必须严格遵循已确定的模板简报和生成的 `design_spec.md`：
- **画布尺寸**：viewBox 匹配设计规范
- **色彩方案**：使用规范中的主色、次色和强调色
- **字体方案**：使用规范中声明的各角色字体族
- **布局原则**：边距和间距符合规范

如存在 PPTX 导入输出：
- 优先使用导入的主题色彩和字体，而非视觉猜测值
- 直接复用导出的 `assets/` 图片——`svg/` 中的 `<image>` 引用已指向规范文件
- 将 `manifest.pageTypeCandidates` 的页面类型候选视为提示而非保证

**前置条件**：

- 提供 PPTX 导入输出时，在读取 `<import_workspace>/svg/` 下所有文件（包括 `master_*.svg`、`layout_*.svg` 和每个 `slide_*.svg`）之前，不得生成任何模板 SVG 或 `design_spec.md`
- 模板生成开始前，明确报告已读取的幻灯片索引

### 2.1 PPTX 导入简化规则

导入的 PPTX 是**参考来源**，不是直接转换目标。

应该做：
- 保留品牌素材、重复背景和稳定的结构性图案
- 将布局重建为符合 PPT Master 约束的干净 SVG 结构
- 将重复装饰片段简化为少量可维护的 SVG 元素
- 当原始装饰层过于复杂无法干净重建时使用背景图片素材
- 使用清理过的 slide SVG 参考来检查排版、间距、文字层级和固定装饰结构（仅在事实元数据已锚定后）
- 读取 `svg/` 下的所有参考 SVG——`master_*.svg`、`layout_*.svg` 和每个 `slide_*.svg`（不论幻灯片数量）。母版/版式文件描述演示的共享视觉语言（先读）；slide 文件描述逐页内容（后读）。部分覆盖会降低模板保真度。
- 将采用的素材重命名为语义化名称（`cover_bg.png`、`brand_emblem.png`）而非将原始 `image3.png` 带入最终模板

不应做：
- 尝试逐一翻译每个 PowerPoint 形状、组、阴影或装饰片段
- 当 PPT 特有复杂度使生成的 SVG 脆弱或难以编辑时仍试图镜像
- 引入不会实质改善模板复用的密集低价值矢量细节

### 3. 占位符标记

> **Mirror 模式跳过本节。** Mirror SVG 是源 flat 幻灯片的逐字复制——不含 `{{}}` 标记。Executor 将其作为视觉参考并就地编辑文本（见 [executor-base.md](executor-base.md) §1.1）。本节余下内容仅适用于 `standard` 和 `fidelity`。

使用清晰的占位符标记表示可替换内容：

```xml
<!-- 文本占位符 -->
<text x="80" y="320" fill="#FFFFFF" font-size="48" font-weight="bold">
  {{TITLE}}
</text>

<!-- 内容区占位符（仅内容页） -->
<rect x="40" y="90" width="1200" height="550" fill="#FFFFFF" rx="8"/>
<text x="640" y="365" text-anchor="middle" fill="#CBD5E1" font-size="16">
  {{CONTENT_AREA}}
</text>
```

### 4. 占位符参考（标准约定，可按模板覆盖）

这是全库使用的**默认词汇表**。新创建的模板应优先使用这些名称，以便消费库的项目找到熟悉的槽位；设计者可在风格确实需要不同词汇时替换或扩展（如咨询类演示用 `{{KEY_MESSAGE}}` 代替 `{{PAGE_TITLE}}`；品牌封面可能需要 `{{BRAND_LOGO}}`）。

`svg_quality_checker.py --template-mode` 在页面缺少其类型对应标准占位符时发出**建议性警告**。要消除警告——并记录模板的实际契约——在 `design_spec.md` frontmatter 中声明 `placeholders:` 映射：

```yaml
placeholders:
  01_cover: ["{{TITLE}}", "{{SUBTITLE}}", "{{BRAND_LOGO}}"]
  03_content: ["{{KEY_MESSAGE}}", "{{CONTENT_AREA}}"]
  03a_content_dual_col: []   # 明确断言"无必需占位符"
```

| 占位符                | 用途       | 适用页面           | 约定角色 |
| --------------------- | ---------- | ------------------ | -------- |
| `{{TITLE}}`           | 主标题     | 封面               | 默认     |
| `{{SUBTITLE}}`        | 副标题     | 封面               | 默认     |
| `{{DATE}}`            | 日期       | 封面               | 默认     |
| `{{AUTHOR}}`          | 作者/组织  | 封面               | 默认     |
| `{{CHAPTER_NUM}}`     | 章节编号   | 章节页             | 默认     |
| `{{CHAPTER_TITLE}}`   | 章节标题   | 章节页             | 默认     |
| `{{CHAPTER_DESC}}`    | 章节描述   | 章节页             | 可选     |
| `{{PAGE_TITLE}}`      | 页面标题   | 内容页             | 默认     |
| `{{CONTENT_AREA}}`    | 内容区域   | 内容页             | 默认     |
| `{{PAGE_NUM}}`        | 页码       | 内容页、结束页     | 默认     |
| `{{KEY_MESSAGE}}`     | 核心观点   | 内容页（咨询风格） | 风格专属 |
| `{{SECTION_NAME}}`    | 章节名称   | 内容页页脚         | 可选     |
| `{{SOURCE}}`          | 数据来源   | 内容页页脚         | 可选     |
| `{{THANK_YOU}}`       | 感谢语     | 结束页             | 默认     |
| `{{CONTACT_INFO}}`    | 联系信息   | 结束页             | 默认     |
| `{{ENDING_SUBTITLE}}` | 结束副标题 | 结束页             | 可选     |
| `{{CLOSING_MESSAGE}}` | 结语       | 结束页             | 风格专属 |
| `{{COPYRIGHT}}`       | 版权信息   | 结束页             | 可选     |

目录页在**新创建的库模板**中使用索引占位符：

- `{{TOC_ITEM_1_TITLE}}`, `{{TOC_ITEM_1_DESC}}`
- `{{TOC_ITEM_2_TITLE}}`, `{{TOC_ITEM_2_DESC}}`
- ...

新模板**不要**创建 `{{CHAPTER_01_TITLE}}` 等新 TOC 占位符族。现有模板可能包含遗留占位符变体，但新库资产应收敛到索引 TOC 契约。

变体默认复用其父类型的占位符集：每个 `03*_content*.svg` 共享上述内容占位符列表，除非规范 frontmatter 为该特定文件名声明了覆盖。

从导入的 PPTX 参考重建时，占位符插入优先于视觉模仿。如原始布局空间不足以放置标准占位符，调整布局而非自造一次性占位符族——或者，如偏差有意义且合理，在 frontmatter 中声明。

---

## 输出要求

### 文件保存位置

Standard 模式（默认）：

```
templates/layouts/<template_name>/
├── design_spec.md     # 设计规范（必需）
├── 01_cover.svg
├── 02_chapter.svg
├── 02_toc.svg          # 可选
├── 03_content.svg
├── 04_ending.svg
└── *.png / *.jpg       # 图片素材（如有）
```

Fidelity 模式添加变体和扩展页面，例如：

```
templates/layouts/<template_name>/
├── design_spec.md
├── 01_cover.svg
├── 02a_chapter_full.svg
├── 02b_chapter_minimal.svg
├── 02_toc.svg
├── 03a_content_two_col.svg
├── 03b_content_data_card.svg
├── 03c_content_quote.svg
├── 04_ending.svg
├── 05_section_break.svg
└── *.png / *.jpg
```

Mirror 模式按源顺序每页输出一个 SVG：

```
templates/layouts/<template_name>/
├── design_spec.md
├── 001_cover.svg
├── 002_toc.svg
├── 003_content.svg
├── 004_content.svg
├── 005_chapter.svg
├── 006_content.svg
├── ...
├── 049_content.svg
├── 050_ending.svg
└── *.png / *.jpg
```

文件名通过 3 位数字前缀保持源幻灯片顺序；`<page_type>` 来自 `manifest.json` `pageTypeCandidates`。Mirror SVG 中不出现 `{{}}` 占位符。

### 模板预览

每个模板生成后，提供简要状态汇总表。

如模板基于 PPTX 导入输出，简要说明：
- 哪些提取素材被直接复用
- 哪些复杂原始装饰被有意简化
- 是否有页面类型映射需要超出导入启发式的判断

---

## 使用预制模板库（可选）

如已存在合适的模板资源，直接使用而非新建：

1. **复制模板**：将模板文件复制到项目的 `templates/` 目录
2. **调整色彩**：按项目设计规范修改颜色
3. **定制**：进行项目特定调整

本节描述下游复用。`Template_Designer` 角色自身负责首先创建或规范化可复用的库资产。

**库结构示例**（查询 `templates/layouts/layouts_index.json`）：

```
templates/layouts/
├── google_style/      # Google Material Design 风格
├── academic_defense/  # 学术答辩风格
└── 招商银行/          # 招商银行品牌风格
```

---

## 阶段完成检查点

```markdown
## Template_Designer 阶段完成

- [x] 已读取 `references/template-designer.md`
- [x] 复制模式已确认：`standard` | `fidelity` | `mirror`
- [x] `design_spec.md §V 页面名册` 中列出的每个页面已保存至 `templates/layouts/<template_name>/`
- [x] 命名约定已应用（standard / fidelity: 字母后缀变体；mirror: `<NNN>_<page_type>.svg`）
- [x] 模板遵循设计规范（色彩、字体、布局）
- [x] 占位符标记清晰且标准化（standard / fidelity）；mirror SVG **不含** `{{}}` 标记
- [ ] **下一步**：验证素材并在 `layouts_index.json` 中注册模板（含 `pages` 字段）
```
