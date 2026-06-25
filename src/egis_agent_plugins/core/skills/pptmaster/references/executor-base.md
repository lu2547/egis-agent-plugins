# 执行器通用指南

> 风格专属内容在对应的 `executor-{style}.md` 中。技术约束在 shared-standards.md 中。

---

## 1. 模板遵循规则

### 1.0 生成前批量阅读

**硬性规则**：在生成第一页 SVG 前，批量阅读本 deck 将引用的所有模板 SVG。前期一次性读完，生成过程中不得重复阅读。

| 来源列表                                                                | 阅读路径                                     |
| ----------------------------------------------------------------------- | -------------------------------------------- |
| 所选模板的 `design_spec.md`（读 frontmatter 以检测 `replication_mode`） | `templates/<chosen_template>/design_spec.md` |
| `spec_lock.md page_layouts` 中每个不重复的 `<basename>`                 | `templates/<chosen_template>/<basename>.svg` |
| `spec_lock.md page_charts` 中每个不重复的图表名                         | `templates/charts/<chart_name>.svg`          |
| `design_spec.md §VII` 中未被上述覆盖的图表类型                          | `templates/charts/<chart_name>.svg`          |

**禁止 — 生成过程中重复阅读**：
- 本批次已加载的布局 SVG
- 本批次已加载的图表 SVG

`spec_lock.md` 是唯一需要逐页重读的文件（§2.1）。

**例外**：用户中途加页或更换模板，引入原始批次中没有的 basename/chart → 读取该新文件一次，继续。

> 注：批量前缀读取留在缓存提示前缀中；逐页的 `spec_lock.md` 重读追加在其下方并受益于该缓存。按需零散读取布局/图表 SVG 会使下游缓存失效，且位于压缩易失的中间上下文区域。

通过 `spec_lock.md page_layouts`（权威来源）解析每页的模板 SVG。下方的旧版页面类型表是**最后兜底**，仅用于缺少 `page_layouts` 的旧项目。

**解析顺序（逐页）：**

1. **镜像模式模板**（模板的 `design_spec.md` frontmatter 含 `replication_mode: mirror`）→ 见下方 §1.1。该页作为**视觉参考**使用，非占位壳。
2. `spec_lock.md page_layouts` 有 `P<NN>: <basename>` → 继承 `templates/<chosen_template>/<basename>.svg` 的结构（已在 §1.0 中加载）。
3. `page_layouts` 存在但当前页**无条目** → **自由设计**，不继承模板。
4. `page_layouts` 整节缺失（旧项目）**且** `templates/` 目录存在 → 回退到下方页面类型表，按 SVG 文件名关键词匹配（cover/chapter/content/ending/toc）。首次使用时读取（若 §1.0 批次未覆盖）。
5. 完全无模板 → 自由设计。

> 注：`page_layouts` 可区分现代模板的多种内容变体（如 `graduation_defense` 有 8 种）；旧版表无法做到。

### 1.1 镜像模式模板 — 参考式使用

当项目所选模板为 `mirror` 模板（`design_spec.md` frontmatter 声明 `replication_mode: mirror`）时，执行器切换到**参考式**使用路径，绕过占位符替换：

1. **逐页参考选择** — 策略师通过 `spec_lock.md page_layouts` 为每个项目页面选择一个镜像页（如 `P04: 015_content`）。basename 为镜像文件名去扩展名；策略师通过阅读 `design_spec.md §V Page Roster` 描述做出选择，非猜测。
2. **复制而非填充** — 打开引用的镜像 SVG（已在 §1.0 加载）。**将其作为项目页面的起点复制**，然后原地编辑文本元素以表达 `P<NN>` 的项目内容。逐字保留所有非文本元素：背景、装饰形状、精灵裁剪图片、图表、图标使用、颜色值、字体家族、几何、精灵 `<svg viewBox>` 包装器、`<image>` 引用。
3. **可编辑内容** — 表达幻灯片特定内容的 `<text>` / `<tspan>` 元素的可见文本内容（标题、正文、图注、KPI 标签、日期、页码）。用项目文本替换源 deck 的示例文本，来源为 `design_spec.md §IX` 和 `notes/<NN>_*.md`。
4. **不可触碰的内容** — 元素位置、尺寸、字体、颜色、填充、描边、渐变、image href、`<g>` 分组、精灵表 `<svg viewBox>` 包装器、装饰性 `<rect>` / `<path>` / `<circle>` / `<polygon>` 形状、`<use data-icon="...">` 标记、嵌入的图表数据结构。镜像的价值在于保留源 deck 的视觉识别——任何几何/装饰偏移都违背初衷。
5. **内容适配** — 镜像页由策略师选择是因为其布局匹配内容槽位。若项目 `P<NN>` 的内容确实需要比镜像页更多/更少的项目（如镜像显示 3 张 KPI 卡片，项目有 4 个指标），保持镜像页的视觉节奏，削减一个指标适配或分拆到两页——**不要**重构镜像页的网格。若两者皆不可行，输出 `warning: P<NN> content does not fit mirror reference <basename>; suggest different reference page` 并以最接近的编辑继续。
6. **无 `{{}}` 替换** — 镜像 SVG 不含占位标记。不要搜索 `{{TITLE}}` / `{{CONTENT_AREA}}` 等；不要自创占位符。镜像契约为"逐字源 + 原地文本编辑"。
7. **输出文件名** — 遵循标准项目 SVG 命名规范（`<NN>_<page_name>.svg`，`<NN>` 匹配项目页面索引，非镜像源索引）。镜像文件名是*参考*，非*输出*。

**检测镜像模式**：在 §1.0 批量阅读时读取所选模板的 `design_spec.md` frontmatter。若 `replication_mode: mirror`，每个命中 `page_layouts` 的页面遵循上述 §1.1；无 `page_layouts` 条目的页面仍回退到自由设计（解析规则 3）。

**镜像 + 图表页**：镜像 SVG 中的图表结构已绘制完毕（轴线、系列、标签）。将其视为视觉参考——替换数据标签和系列文本以匹配项目图表规格，但不从 `templates/charts/<name>.svg` 基线重绘。镜像模板的 `page_charts` 条目通常因此缺失。

**旧版兜底表**（仅在 `page_layouts` 缺失时使用）：

| 页面类型 | 对应模板         | 遵循规则                                   |
| -------- | ---------------- | ------------------------------------------ |
| 封面     | `01_cover.svg`   | 继承背景、装饰元素、布局结构；替换占位内容 |
| 章节页   | `02_chapter.svg` | 继承编号风格、标题位置、装饰元素           |
| 内容页   | `03_content.svg` | 继承页头/页脚样式；**内容区域可自由排版**  |
| 结束页   | `04_ending.svg`  | 继承背景、感谢语位置、联系信息布局         |
| 目录     | `02_toc.svg`     | **可选**：继承目录标题、列表样式           |

### 页面-模板映射声明（必须输出）

生成每页前，输出使用的模板：

```
📝 **模板映射**: `templates/<chosen_template>/03a_content_image_text.svg`（或"None（自由设计）"）
🎯 **遵循规则 / 布局策略**: [具体描述]
```

- **内容页**：模板仅定义页头/页脚；内容区域自由
- **无模板**：完全按设计规格生成

---

## 2. 设计参数确认（强制步骤）

生成第一页 SVG 前，输出确认清单：画布尺寸、正文字号、配色方案（主/辅/点缀色 HEX）、字体方案。防止规格/执行漂移。

### 2.1 逐页 spec_lock 重读（强制）

> 长 deck 因上下文压缩，中途会偏离声明的调色板/图标。`spec_lock.md` 是规范执行参考——逐页重读以绕过模型记忆。

**硬性规则**：生成**每页** SVG 前，执行 `read_file <project_path>/spec_lock.md`。仅使用该文件中的值，不依赖记忆。若上下文已被自动压缩，同时执行 `read_file <project_path>/design_spec.md` 获取当前页的 §IX 摘要。

**若 `spec_lock.md` 缺失**：输出 `warning: spec_lock.md missing — generating without execution lock` 一次，然后使用 `design_spec.md` 中的值继续。仅预期用于旧项目；新项目必须有此文件（见 [strategist.md](strategist.md) §3 步骤 4）。

**禁止 — lock 之外的值**：

- 颜色（fill / stroke / stop-color）必须来自 `colors`
- 图标必须来自 `icons.inventory`；库必须等于 `icons.library`
- 字体家族来自 `typography`：若声明了角色覆盖（`title_family` / `body_family` / `emphasis_family` / `code_family`）则使用，否则回退到 `font_family`
- 字号遵循以 `typography.body` 为锚的**梯度**，非封闭菜单。声明的槽位适合时使用。中间值（如 40px 核心数字、13px 注释）在比率落于该角色区间内时允许（见 `design_spec.md §IV 梯度表`）。超出所有区间的值需先扩展 lock。
- 图片必须引用 `images` 下列出的文件；不得虚构文件名

若某页需要 `spec_lock.md` 中没有的值，主动提出——不得静默虚构。

**逐页布局节奏 — `page_rhythm` 节**：

绘制每页前，查找其在 `page_rhythm` 中的条目（key 格式 `P<NN>` 匹配 `design_spec.md` §IX 中的页面索引）并应用对应的布局纪律：

| 标签        | 布局纪律                                                                                                                                                                                                                                                                                                                                                                      |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `anchor`    | 结构页（封面/章节/目录/结束页）。严格遵循匹配的模板。                                                                                                                                                                                                                                                                                                                         |
| `dense`     | 信息密集页。允许卡片网格、多栏布局、KPI 仪表板、表格、图表。这是基线行为。                                                                                                                                                                                                                                                                                                    |
| `breathing` | 低密度冲击页。避免**多卡片网格布局**——不要将内容组织为多个并排圆角容器（3 卡片行、4 卡片 KPI 网格、2×2 矩阵渲染为卡片）。使用裸文本块、分割线、留白或全出血图片作为内容结构。单个圆角视觉元素（主图圆角、标注、标签、一个强调块）可以——规则针对网格结构，非 `rx` 属性。比例跟随信息权重（非预设比率）。典型形式：主引言、单个大数字+一行解读、全出血图片+浮动图注、章节过渡。 |

> 没有节奏变化，每页都默认为卡片网格（"AI 生成感"）。`page_rhythm` 是唯一在上下文压缩中存活的叙事杠杆。

**缺少 `page_rhythm` 节** → 输出 `warning: spec_lock.md missing page_rhythm — defaulting all pages to dense` 一次，所有页面回退 `dense`。

**当前页无标签** → 静默回退 `dense`。不得虚构标签。

**逐页模板查找 — `page_layouts` 节**：

绘制每页前，查找其在 `page_layouts` 中的条目以决定继承哪个 basename（SVG 本身已在 §1.0 加载）：

- 有条目（如 `P04: 03a_content_image_text`）→ 继承上下文中对应的 SVG。basename **必须匹配**所选模板目录中的实际文件；若不匹配，输出 `warning: page_layouts P<NN> references missing file <basename>.svg — falling back to free design` 并继续。
- 当前页无条目 → 自由设计，不继承。**非错误**——策略师有意留此页自由。
- 整节缺失 → 见 §1 兜底（旧版页面类型匹配）。

**不得**虚构布局条目，**不得**仅因 `templates/` 存在就假设有模板——若 `page_layouts` 存在但对当前页沉默，该沉默即是指令。

**逐页图表参考 — `page_charts` 节**：

绘制每页前，查找其在 `page_charts` 中的条目以决定应用哪个图表结构（SVG 本身已在 §1.0 加载）：

- 有条目（如 `P09: timeline_horizontal`）→ 适配上下文中对应的图表 SVG。应用项目配色/字体/密度；不逐字复制。需要时交叉参考 `templates/charts/charts_index.json` 获取图表用途摘要。
- 当前页无条目 → 该页无图表，或图表未匹配任何目录模板（策略师的 `no-template-match` 兜底）。从零设计可视化，以 `design_spec.md §VII` 为指导。
- 整节缺失 → 本 deck 无图表页。

---

## 3. 执行指南

- **亲密性**：相关元素紧密分组；不相关组之间留出间距
- **规格遵循**：遵循规格中的配色、布局、画布格式和字体排版
- **模板结构**：若模板存在，继承视觉框架
- **主 agent 所有权**：SVG 生成必须在主 agent 中运行（非子 agent）——页面共享上游上下文以实现跨页视觉连续性
- **生成节奏**：先锁定全局设计上下文，然后在一个连续上下文中顺序生成页面。不分批（如每次 5 页）。
- **分阶段批量生成**（推荐）：
  1. **视觉构建阶段**：顺序生成所有 SVG 页面以确保视觉一致性。图表标记使用布局判断进行初稿。**必须嵌入绘图区域标记**（按下方 §3.1）——坐标校准是生成后步骤（见 [`workflows/verify-charts.md`](../workflows/verify-charts.md)），依赖这些标记。
  2. **质量检查关卡**：对 `svg_output/` 运行 `python3 scripts/svg_quality_checker.py <project_path>`。任何 `error`（禁用特性、viewBox 不匹配、spec_lock 漂移、非 PPT 安全字体等）必须在问题页面修复后才能继续——重新生成并重新检查。`warning` 级别问题在直接时处理。不得推迟到 `finalize_svg.py` 之后——finalize 会重写 SVG 并掩盖部分违规。
  3. **逻辑构建阶段**：SVG 通过质量检查后，批量生成演讲备注以确保叙事连贯性。

### 3.1 图表绘图区域标记（每个图表页强制）

> [`verify-charts`](../workflows/verify-charts.md) 工作流从 `design_spec.md §VII` 枚举图表页，然后读取每页的绘图区域标记以供 `svg_position_calculator.py` 使用。缺少标记 → verify-charts 必须从轴线重新推导绘图区域，每次运行都要付出代价。

每个包含数据可视化图表的 SVG 页面必须在 `<g id="chartArea">` 内包含绘图区域标记，置于**轴线之后**且**第一个数据元素之前**（柱、线、面积、点）。

**矩形绘图区域**（bar / horizontal_bar / grouped_bar / stacked_bar / line / area / stacked_area / scatter / waterfall / pareto / butterfly）：

```xml
<!-- chart-plot-area: x_min,y_min,x_max,y_max -->
```

**径向图表**（pie / donut / radar）：

```xml
<!-- chart-plot-area: pie | center: cx,cy | radius: r -->
<!-- chart-plot-area: donut | center: cx,cy | outer-radius: r1 | inner-radius: r2 -->
<!-- chart-plot-area: radar | center: cx,cy | radius: r -->
```

**坐标值确定方法**：

| 值       | 推导方法                                                    |
| -------- | ----------------------------------------------------------- |
| `x_min`  | Y 轴线的 X 坐标（最左数据边界）                             |
| `y_min`  | 最顶部网格线的 Y 坐标（最高数据边界）                       |
| `x_max`  | 最右轴端点或网格线的 X 坐标                                 |
| `y_max`  | X 轴基线的 Y 坐标                                           |
| `cx, cy` | 饼图/环形图/雷达图中心点（考虑 `transform="translate()"` ） |
| `r`      | 图表外半径                                                  |

**逐页验证** — 写完每个图表 SVG 后，确认标记存在：

```bash
grep "chart-plot-area" <project_path>/svg_output/<current_page>.svg
```

> `templates/charts/` 中所有图表模板均包含此标记作为参考。若你在绘制图表时标记缺失，说明有 bug。
- **技术规格**：SVG/PPT 约束见 [shared-standards.md](shared-standards.md)
- **卡片容器 — 使用已文档化的模式**：当内容页需要分区卡片（4 象限、并列方面、能力块、信息卡片）时，使用 [`templates/charts/CHART_STYLE_GUIDE.md`](../templates/charts/CHART_STYLE_GUIDE.md) §11 中编纂的模式——半圆角分区标签（§11.1）、无描边嵌套卡片边框（§11.2）、卡片网格骨架（§11.3）、对角虚线连接器用于跨象限关系（§11.5）、地面锚点椭圆作为非滤镜深度标记（§11.6）、双向交互箭头用于配对协议（§11.7）。不要重新发明"着色全圆角矩形 + 白色覆盖矩形隐藏底部圆角"的 hack；它存在于旧模板中但破坏 SVG→PPTX 颜色编辑。参考模板：[`labeled_card.svg`](../templates/charts/labeled_card.svg)、[`quadrant_text_bullets.svg`](../templates/charts/quadrant_text_bullets.svg)、[`kpi_cards.svg`](../templates/charts/kpi_cards.svg)、[`matrix_2x2.svg`](../templates/charts/matrix_2x2.svg)、[`team_roster.svg`](../templates/charts/team_roster.svg)、[`client_server_flow.svg`](../templates/charts/client_server_flow.svg)。
- **语义形状优于预设堆叠**：当幻灯片需要表达"上升/汇聚/突破/层叠"——即超越通用箭头的关系——优先使用单个自定义 `<polygon>` 或 `<path>` 以几何方式编码语义，而非堆叠多个预设箭头。一个汇聚尖端路径或讲台多边形比三个箭头指向一个标签更易读。许多导入的企业 deck 中有此技法；见 `projects/01_template_import/svg_output/slide_01.svg` shape-158 作为参考（渐变填充向内指向箭头）。不要将这些编纂为模板——它们是页面特定的；规则仅为"在堆叠预设前考虑多边形"。
- **视觉深度 — 通过克制实现**：层次深度来自节奏（平 vs 悬浮、密 vs 疏），而非到处加阴影。每页最多对 2-3 个真正悬浮的元素应用阴影（照片上的卡片、主 CTA、叠加层）；同级网格卡片、分割线、正文容器保持平坦。在使用阴影之前先考虑字重、间距、强调条、微妙色调。完整规则见 shared-standards.md §6。

### SVG 文件命名规范

格式：`<NN>_<page_name>.svg`（两位数字从 01 起；名称匹配 deck 语言和设计规格中的页面标题）。

示例：`01_封面.svg` / `02_目录.svg` / `03_核心优势.svg`；`01_cover.svg` / `02_agenda.svg` / `03_key_benefits.svg`。

---

## 4. 图标使用

策略师选择库和清单；执行器只负责实现。库详情和单库规则：[`../templates/icons/README.md`](../templates/icons/README.md)。本节定义占位语法。

**内置图标 — 占位方法（推荐）**：

```xml
<!-- chunk（直线几何，锐角，结构感） -->
<use data-icon="chunk/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- tabler-filled（贝塞尔曲线，圆润有机） -->
<use data-icon="tabler-filled/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- tabler-outline（线条风格——仅屏幕显示 deck） -->
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- phosphor-duotone（单色 + 20% 底板——柔和深度无实心重量） -->
<use data-icon="phosphor-duotone/house" x="100" y="200" width="48" height="48" fill="#005587"/>

<!-- simple-icons（品牌 Logo——与 deck 主风格库并用，仅用于真实公司/产品标识） -->
<use data-icon="simple-icons/github" x="100" y="200" width="48" height="48" fill="#181717"/>

<!-- tabler-outline 细/粗描边（仅描边类库） -->
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587" stroke-width="1.5"/>
<use data-icon="tabler-outline/home" x="100" y="200" width="48" height="48" fill="#005587" stroke-width="3"/>
```

> ⚠️ **颜色**：`<use data-icon="...">` 上始终使用 `fill="#HEX"`。不得使用 `stroke` 或 `fill="none"`，即使是描边类库。
>
> **stroke-width**（仅描边类库，当前为 `tabler-outline`）：允许值 `{1.5, 2, 3}`。若 `spec_lock.md icons.stroke_width` 已声明，所有占位符必须 deck 全局使用该值。缺省时默认 `2`（旧版）。非描边库忽略此项。
>
> 图标由 `finalize_svg.py` 自动嵌入——无需手动运行 `embed_icons.py`。

**搜索图标** — 使用终端，零 token 开销：
```bash
ls skills/ppt-master/templates/icons/chunk/ | grep home
ls skills/ppt-master/templates/icons/tabler-filled/ | grep home
ls skills/ppt-master/templates/icons/tabler-outline/ | grep chart
ls skills/ppt-master/templates/icons/phosphor-duotone/ | grep house
ls skills/ppt-master/templates/icons/simple-icons/ | grep github
```

**抽象概念 → 图标名**（chunk 的名称；tabler 库使用各自对应名——用 `ls | grep` 验证）：

| 概念        | chunk                     | tabler-filled / tabler-outline |
| ----------- | ------------------------- | ------------------------------ |
| 增长 / 上升 | `arrow-trend-up`          | 同名                           |
| 下降 / 减少 | `arrow-trend-down`        | 同名                           |
| 成功 / 完成 | `circle-checkmark`        | `circle-check`                 |
| 警告 / 风险 | `triangle-exclamation`    | `alert-triangle`               |
| 创新 / 想法 | `lightbulb`               | `bulb`                         |
| 策略 / 目标 | `target`                  | 同名                           |
| 效率 / 速度 | `bolt`                    | 同名                           |
| 协作 / 团队 | `users`                   | 同名                           |
| 设置 / 配置 | `cog`                     | `settings`                     |
| 安全 / 信任 | `shield`                  | 同名                           |
| 金钱 / 财务 | `dollar`                  | `currency-dollar`              |
| 时间 / 截止 | `clock`                   | 同名                           |
| 位置 / 区域 | `map-pin`                 | 同名                           |
| 沟通        | `comment`                 | `message`                      |
| 分析 / 数据 | `chart-bar`               | 同名                           |
| 流程 / 循环 | `arrows-rotate-clockwise` | `refresh`                      |
| 全球 / 世界 | `globe`                   | `world`                        |
| 卓越 / 奖项 | `star`                    | 同名                           |
| 扩展 / 规模 | `maximize`                | 同名                           |
| 问题 / 缺陷 | `bug`                     | 同名                           |

> 对自明名称（home、user、file、search、arrow 等）——直接 `grep chunk/` 即可，无需查表。

> ⚠️ **图标验证**：仅使用设计规格批准清单中的图标。使用前通过 `ls | grep` 验证每个。同一 deck 内混用库是**禁止的**。

---

## 5. 可视化参考

**VII. 可视化参考列表**中引用的图表 SVG 通过 §1.0 批量阅读一次性加载。本节仅管理适配。

**硬性规则**：适配已加载的图表 SVG；不凭记忆即兴发挥，也不逐字照搬。应用项目配色、字体、内容；保留可视化类型。

**适配规则**：
- **保留**：按规格指定的可视化类型（bar/line/pie/timeline/process/framework…）
- **适配**：数据、标签、颜色（项目配色）、尺寸
- **自由调整**：构图、轴范围、网格、图例、间距、装饰——只要图表保持准确可读
- **禁止**：无规格依据地更改可视化类型；遗漏大纲中的数据点或结构元素

> 模板：`templates/charts/`（70 种类型）。索引：`templates/charts/charts_index.json`

### 5.1 图表坐标校准

坐标校准作为**独立的生成后工作流**运行，不在执行器管线内。SVG 生成完成后，若 deck 包含数据图表，在后处理前运行 [`workflows/verify-charts.md`](../workflows/verify-charts.md)。

执行器此处的唯一义务是上游：在初稿时为每个图表页嵌入 `<!-- chart-plot-area ... -->` 标记（§3.1）。verify-charts 从 `design_spec.md §VII`（权威 deck 计划）枚举图表页，并使用标记供 `svg_position_calculator.py` 使用。

> 不要在初稿阶段运行 `svg_position_calculator.py`。计算器校准已生成的 SVG 与其声明的绘图区域；SVG 存在前运行它无物可比。

---

## 6. 图片处理

按设计规格图片资源列表中的状态处理图片。状态枚举和生命周期：[`svg-image-embedding.md`](svg-image-embedding.md)。

| 状态             | 来源               | 处理方式                                 |
| ---------------- | ------------------ | ---------------------------------------- |
| **Existing**     | 用户提供           | 直接从 `../images/` 目录引用             |
| **Placeholder**  | 尚未准备           | 使用虚线边框占位符                       |
| **Needs-Manual** | 获取失败且文件缺失 | 使用虚线边框占位符（除非预期文件已存在） |

**引用语法**：见 [`svg-image-embedding.md`](svg-image-embedding.md)。

**占位符**：虚线边框 `<rect stroke-dasharray="8,4" .../>` + 描述文字

**`no-crop` 图片**：当 `spec_lock.md images` 条目以 ` | no-crop` 结尾时，将容器尺寸调整为图片原始比率（来自 `analyze_images.py` 或文件尺寸）并使用 `preserveAspectRatio="xMidYMid meet"`。未标记的条目可裁剪——默认使用 `slice`。

---

## 7. 字体使用

权威来源：`spec_lock.md typography`。使用 `font_family` 作为默认；若声明了角色覆盖（`title_family` / `body_family` / `emphasis_family` / `code_family`）则使用覆盖值。

若 `spec_lock.md` 缺失，查阅 [`strategist.md`](strategist.md) §g——不得自创 stack。

**硬性规则**：每个 SVG `font-family` stack 必须以预装字体家族收尾（Microsoft YaHei / SimHei / SimSun / Arial / Calibri / Segoe UI / Times New Roman / Georgia / Consolas / Courier New / Impact / Arial Black）。PPTX 无运行时回退——缺失字体降级为 Calibri。

---

## 8. 演讲备注生成框架

### 任务 1. 生成完整演讲备注文档

所有 SVG 页面定稿后，进入逻辑构建阶段，将完整备注写入 `notes/total.md`。批量写作（非逐页）使过渡衔接更连贯。

**纯口述叙述**：备注由 `notes_to_audio.py`（TTS）逐字朗读。只写应被说出的内容。不要有可见标记、标注元信息行、枚举要点列表、时长注释——标题之外的任何内容都会被语音化。

**逐页结构**：`# <number>_<page_title>` 标题（`#` 标题行是 TTS 前唯一被剥离的内容），页间用 `---` 分隔。正文为 2-5 句自然语句，传达该页核心信息。页间过渡写入开头句子作为自然行文（"接下来……" / "Having framed X, let's turn to Y"）——不用方括号 `[过渡]` / `[Transition]` 标签。

**具体示例** — 同一结构适用于任何语言；用目标语言自然书写即可。

中文 deck：

```
# 02_市场格局

在明确了行业背景之后，我们来看具体的市场格局。当前线上零售集中度持续上升，前三大平台合计份额已经达到百分之六十八，腰部玩家正在被快速挤压，留给新进入者的窗口期不超过十八个月。这意味着我们的策略必须聚焦，而不是铺开。
```

英文 deck：

```
# 02_market_landscape

Having framed the industry backdrop, let's look at the actual market landscape. Online retail concentration keeps rising — the top three platforms now hold sixty-eight percent of combined share, mid-tier players are being squeezed fast, and the window for new entrants is under eighteen months. This means our strategy has to focus, not spread.
```

> 日本語 / 한국어 / 其他语言：照搬同样的结构，用对应语言自然书写即可。

**数字可读性**：TTS 逐字朗读数字和符号。当字面发音不自然时，优先使用目标语言的完整写法（如中文"百分之六十八"比"68%"更自然；"1-2分钟"会被读成"一减二分钟"）。英文中普通整数和百分比可直接使用。

**常见错误避免**：
- 在文本中留下任何方括号阶段标记（`[过渡]` / `[Transition]` / `[Pause]` / `[Data]` / `[Scan Room]` / `[Interactive]` / `[Benchmark]` 等）——它们会被逐字朗读。
- 添加 `要点：① …` / `Key points: (1) …` / `时长：2分钟` / `Duration: 2 minutes` / `Flex: …` 行——TTS 会说出"要点 一 …"。
- 在同一 deck 备注中混用语言。

### 任务 2. 拆分为逐页备注文件

将 `notes/total.md` 自动拆分为 `notes/` 下的逐页文件。

**命名**：匹配 SVG 名称（`01_cover.svg` → `notes/01_cover.md`）；`slide01.md` 也支持（旧版）。

---

## 9. 完成后下一步

> **自动续行**：视觉构建阶段（所有 SVG 页面）和逻辑构建阶段（所有备注）完成后，执行器直接进入后处理管线。

**后处理与导出**（与 [shared-standards.md §5](shared-standards.md) 相同的规范管线）：

```bash
# 1. 拆分演讲备注
python3 scripts/total_md_split.py <project_path>

# 2. SVG 后处理（自动嵌入图标、图片等）
python3 scripts/finalize_svg.py <project_path>

# 3. 导出 PPTX
python3 scripts/svg_to_pptx.py <project_path>
# 输出：
#   exports/<project_name>_<timestamp>.pptx           ← 主原生 pptx
#   backup/<timestamp>/<project_name>_svg.pptx        ← SVG 快照
#   backup/<timestamp>/svg_output/                    ← 执行器 SVG 源备份
```
