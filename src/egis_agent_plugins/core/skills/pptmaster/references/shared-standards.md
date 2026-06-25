# 共享技术标准

PPT Master 通用技术约束，消除跨角色文件重复。

---

## 1. SVG 禁用特性黑名单

以下在生成的 SVG 中**禁止使用**——否则 PPT 导出会失败：

### 1.0 文本字符：必须是合法 XML

SVG 是严格 XML。所有文本和属性值遵循两条规则：

| 字符类别                                                                 | 必须使用的形式                                                                               | 禁止形式                                                                                                 |
| ------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| 排版与符号（破折号、版权号、箭头、间隔号、不换行空格、全角标点、emoji…） | **原生 Unicode 字符** — 直接写 `—` `–` `©` `®` `→`                                           | HTML 命名实体 — `&mdash;` `&ndash;` `&copy;` `&reg;` `&rarr;` `&middot;` `&nbsp;` `&hellip;` `&bull;` 等 |
| XML 保留字符（`&`、`<`、`>`、`"`、`'`）                                  | **仅用 XML 实体** — `&amp;` `&lt;` `&gt;` `&quot;` `&apos;`（如 `R&amp;D`、`error &lt; 5%`） | 裸写 `&` `<` `>`（如 `R&D`、`error < 5%`）                                                               |

一个违规字符即导致文件无效并中止导出。数字引用（`&#160;` / `&#xa0;`）是合法 XML 但不推荐。

**结构性黑名单**（除上述字符规则外）：

| 禁用特性               | 说明                                                      |
| ---------------------- | --------------------------------------------------------- |
| `mask`                 | 遮罩                                                      |
| `<style>`              | 内嵌样式表                                                |
| `class`                | CSS 选择器属性（`<defs>` 内的 `id` 是合法引用，不被禁止） |
| 外部 CSS               | 外部样式表链接                                            |
| `<foreignObject>`      | 嵌入外部内容                                              |
| `<symbol>` + `<use>`   | 符号引用复用                                              |
| `textPath`             | 沿路径文本                                                |
| `@font-face`           | 自定义字体声明                                            |
| `<animate*>` / `<set>` | SVG 动画                                                  |
| `<script>` / 事件属性  | 脚本和交互                                                |
| `<iframe>`             | 嵌入框架                                                  |

> **`marker-start` / `marker-end` 有条件允许** — 约束见 §1.1。转换器将合规标记映射为原生 DrawingML `<a:headEnd>` / `<a:tailEnd>`。
>
> **`clipPath` 用于 `<image>` 有条件允许** — 约束见 §1.2。转换器将合规裁剪形状映射为原生 DrawingML 图片几何（`<a:prstGeom>` 或 `<a:custGeom>`）。
>
> **替代 `<mask>` 效果** — DrawingML 无逐像素 alpha。按效果分流：
> - 图片渐变叠加（暗角/淡化/色调）→ 堆叠 `<rect>` 配合 `<linearGradient>`/`<radialGradient>`（§6 图片叠加）
> - 非矩形图片裁剪（圆形/圆角/六边形）→ `<image>` 上的 `clipPath`（§1.2）
> - 内发光 / 柔边 → `<filter>` 配合 `<feGaussianBlur>`（§6 发光）
> - 投影 → filter 阴影或分层矩形（§6 阴影）
>
> 像素级 alpha 效果（文字镂空图片填充、任意 alpha 合成）无 PPT 路径——在图片源文件准备阶段烘焙进图片。

---

### 1.1 线端标记（有条件允许）

`<line>` 和 `<path>` 元素上的 `marker-start` 和 `marker-end` 仅在引用的 `<marker>` 满足以下所有条件时允许：

| 要求                                                                                                                       | 原因                                                                                                    |
| -------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `<marker>` 元素定义在 `<defs>` 内                                                                                          | 转换器通过 id 索引查找标记定义                                                                          |
| `orient="auto"`                                                                                                            | DrawingML 箭头沿线段切线自动旋转；其他 orient 值无法往返转换                                            |
| 标记形状为以下之一：闭合 3 顶点 path/polygon（三角形）、闭合 4 顶点 path/polygon（菱形）、`<circle>` / `<ellipse>`（椭圆） | 这三种可干净地映射为 DrawingML `type="triangle" / "diamond" / "oval"`。其他形状会被静默丢弃并附带警告。 |
| 标记子元素的 `fill` **匹配**父线段的 `stroke` 颜色                                                                         | DrawingML 中箭头继承线条颜色——fill 不匹配时导出后外观错误。                                             |
| `markerWidth` / `markerHeight` 大致在 `3–15` 范围                                                                          | 映射为 `sm`(<6) / `med`(6–12) / `lg`(>12) 尺寸桶。                                                      |

**使用边界**：

- `marker-start` / `marker-end`：仅用于线条为主体的连接箭头
- 块状 / 粗实 / 实心箭头（箭头主体即视觉对象）使用独立闭合 `<path>` / `<polygon>`；参见 `templates/charts/chevron_process.svg` 或 `templates/charts/process_flow.svg`

**支持的 DrawingML 映射**：

| SVG 标记形状                               | DrawingML 输出                                   |
| ------------------------------------------ | ------------------------------------------------ |
| `<path d="M0,0 L10,5 L0,10 Z"/>`（三角形） | `<a:tailEnd type="triangle" w="med" len="med"/>` |
| `<polygon points="0,0 10,5 0,10"/>`        | `<a:tailEnd type="triangle" w="med" len="med"/>` |
| 4 顶点闭合 path/polygon                    | `<a:tailEnd type="diamond" .../>`                |
| `<circle cx="5" cy="5" r="4"/>`            | `<a:tailEnd type="oval" .../>`                   |

**推荐模板** — 标准箭头定义，可直接复用：

```xml
<defs>
  <marker id="arrowHead" markerWidth="10" markerHeight="10" refX="9" refY="5"
          orient="auto" markerUnits="strokeWidth">
    <path d="M0,0 L10,5 L0,10 Z" fill="#1976D2"/>
  </marker>
</defs>
<line x1="100" y1="200" x2="400" y2="200" stroke="#1976D2" stroke-width="3"
      marker-end="url(#arrowHead)"/>
```

> ⚠️ 无法归类的标记形状（曲线路径、多段、>4 顶点）会被静默丢弃——线条渲染无箭头。对异形箭头使用手动 `<polygon>`。

---

### 1.2 图片裁剪（有条件允许）

`<image>` 元素上的 `clip-path` 在引用的 `<clipPath>` 满足以下条件时允许：

| 要求                                                                                 | 原因                                      |
| ------------------------------------------------------------------------------------ | ----------------------------------------- |
| `<clipPath>` 元素定义在 `<defs>` 内                                                  | 转换器通过 id 索引查找裁剪定义            |
| 包含**单个**形状子元素                                                               | 使用第一个子元素；多个子元素不会合成      |
| 形状为以下之一：`<circle>`、`<ellipse>`、`<rect>`（含 rx/ry）、`<path>`、`<polygon>` | 这些映射为 DrawingML 几何（预设或自定义） |
| **仅用于 `<image>` 元素**                                                            | 非图片元素的 clip-path 是**禁止的**       |

**使用边界**：

- 仅用于 `<image>` 的非矩形裁剪（圆形头像、圆角相框、六边形）
- 不用于形状（`<rect>`/`<circle>`/`<path>`/`<g>`/`<text>`）——直接绘制目标形状。矩形裁切为圆形不如直接画圆形。
- PowerPoint 的 SVG 渲染器不处理 `clipPath`；仅原生 PPTX 转换器支持。

**支持的 DrawingML 映射**：

| SVG 裁剪形状             | DrawingML 输出                             | 用途                     |
| ------------------------ | ------------------------------------------ | ------------------------ |
| `<circle>` / `<ellipse>` | `<a:prstGeom prst="ellipse"/>`             | 圆形头像、椭圆相框       |
| `<rect rx="..."/>`       | `<a:prstGeom prst="roundRect"/>` 含 adj 值 | 圆角矩形照片框           |
| `<path>` / `<polygon>`   | `<a:custGeom>` 含路径命令                  | 六边形、菱形、自定义形状 |

**推荐模板** — 圆形图片裁剪：

```xml
<defs>
  <clipPath id="avatarClip">
    <circle cx="200" cy="200" r="100"/>
  </clipPath>
</defs>
<image href="../images/photo.jpg" x="100" y="100" width="200" height="200"
       clip-path="url(#avatarClip)" preserveAspectRatio="xMidYMid slice"/>
```

**圆角矩形裁剪** — 卡片式图片框：

```xml
<defs>
  <clipPath id="cardClip">
    <rect x="60" y="120" width="400" height="250" rx="16"/>
  </clipPath>
</defs>
<image href="../images/banner.jpg" x="60" y="120" width="400" height="250"
       clip-path="url(#cardClip)" preserveAspectRatio="xMidYMid slice"/>
```

> ⚠️ 非图片元素的 `clip-path` 是禁止的——质量检查器会报错。直接绘制目标几何形状。

---

## 2. PPT 兼容性替代方案

| 禁用语法                       | 正确替代                                                  |
| ------------------------------ | --------------------------------------------------------- |
| `fill="rgba(255,255,255,0.1)"` | `fill="#FFFFFF" fill-opacity="0.1"`                       |
| `<g opacity="0.2">...</g>`     | 对每个子元素单独设置 `fill-opacity` / `stroke-opacity`    |
| `<image opacity="0.3"/>`       | 在图片后叠加 `<rect fill="背景色" opacity="0.7"/>` 遮罩层 |

**助记**：PPT 不识别 rgba、组不透明度、图片不透明度。

> 箭头：连接线优先使用 `marker-end`（§1.1）——转换器生成原生自动旋转箭头。块状/粗实箭头使用独立闭合形状；参见 `templates/charts/chevron_process.svg` 和 `templates/charts/process_flow.svg`。

---

## 3. 画布格式速查

> 完整格式表（演示文稿 / 社交媒体 / 营销物料）及格式选择决策树详见 [`canvas-formats.md`](canvas-formats.md)。

---

## 4. SVG 基础规则

- **viewBox** 必须匹配画布尺寸（`width`/`height` 须匹配 `viewBox`）
- **背景**：使用 `<rect>` 定义页面背景色
- **`<tspan>`** 有两种用途：(1) 手动换行（使用 `dy` 或显式 `y`）；(2) 同行内联格式化（颜色/字重/字号）。`<foreignObject>` 禁止使用。见下方"单逻辑行"规则。
- **字体**：每个 `font-family` stack 必须以预装字体收尾（Microsoft YaHei / SimSun / Arial / Times New Roman / Consolas …）；`@font-face` 禁止。完整规则：[`strategist.md §g`](strategist.md)。
- **样式**：仅内联（`fill=""`、`font-size=""`）；`<style>`/`class` 禁止（`<defs>` 内的 `id` 可以）
- **颜色**：仅 HEX；透明度通过 `fill-opacity`/`stroke-opacity`
- **图片**：`<image href="../images/xxx.png" preserveAspectRatio="xMidYMid slice"/>`
- **图标**：`<use data-icon="<library>/<name>" x="" y="" width="48" height="48" fill="#HEX"/>`（后处理自动嵌入）。始终含库前缀。每 deck 一个风格库（`chunk`/`tabler-filled`/`tabler-outline`/`phosphor-duotone`）；`simple-icons` 仅用于真实品牌标识。见 [`../templates/icons/README.md`](../templates/icons/README.md)。

### 内联文本运行（单逻辑行 = 单个 `<text>`）

一个逻辑行——即使有混合颜色/字重/字号——必须是一个 `<text>` 配合内联 `<tspan>` 子元素。不要使用多个相邻 `<text>` 元素。转换器将每个 `<tspan>` 映射为同一 PPT 文本框内的一个 `<a:r>` run，保持该行作为一个可编辑形状。

✅ **正确** — 一个 `<text>` → 一个文本框含三个 run：

```xml
<text x="100" y="200" font-size="24" fill="#333333">
  实现<tspan fill="#1A73E8" font-weight="bold">10倍</tspan>效率提升
</text>
```

❌ **错误** — 三个并排 `<text>` 元素在 PPT 中变成三个独立文本框（破坏作为一行编辑、有对齐漂移风险、间距脆弱）：

```xml
<text x="100" y="200" font-size="24" fill="#333333">实现</text>
<text x="160" y="200" font-size="24" fill="#1A73E8" font-weight="bold">10倍</text>
<text x="240" y="200" font-size="24" fill="#333333">效率提升</text>
```

**⚠️ 内联 tspan 不得带 `x`/`y`/`dy`** — 这些标记新行，`flatten_tspan` 会将其拆分为独立文本框。`dx` 是安全的（字距调整，保持内联）。仅在 tspan 确实开始新行时设置 `x`/`y`/`dy`。

**多行 `<text>` 配合逐行强调可行**：外层换行 tspan（含 `x` + `dy` 或 `y`）可包含嵌套内联 tspan 用于颜色/字重/字号——转换器遍历嵌套 tspan 并为每个样式段生成一个 run：

```xml
<text x="80" y="190" font-size="18" fill="#333333">
  <tspan x="80" dy="0">完成率<tspan fill="#4CAF50" font-weight="bold">98%</tspan>超预期</tspan>
  <tspan x="80" dy="35">成本降低<tspan fill="#F44336" font-weight="bold">¥120万</tspan></tspan>
</text>
```

❌ **错误** — 通过 `<tspan x="...">` 在同行跳列：

```xml
<text x="100" y="200" font-size="18" fill="#333333">
  <tspan x="100">左列</tspan><tspan x="600" font-weight="bold">右列</tspan>
</text>
```

tspan 上的 `x` 开始新行，拆分为两个独立文本框。双栏布局请写两个 `<text>` 元素。

**默认 — 提亮关键信息。** 统一样式的段落读起来像文字墙。用 `<tspan fill="..." font-weight="bold">` 包裹：

- **数值结果** — 百分比、倍率（`10x`）、绝对金额（`¥120万`）
- **对比** — 得失、前后、目标/实际
- **每句 1-2 个承载洞察的名词** — 传递核心信息的术语

不要高亮：连接词、常见动词、每个名词、装饰性形容词、结构文字（页脚/轴/图例/页码/标签）。

颜色：使用 deck 主品牌色做强调。绿/红仅保留给真实的正面/负面语义。

❌ **错误** — 统一样式段落埋没洞察：

```xml
<text x="80" y="200" font-size="20" fill="#333333">
  2024年公司营收同比增长35%达到12亿元创历史新高
</text>
```

✅ **正确** — 同行，关键数据提亮：

```xml
<text x="80" y="200" font-size="20" fill="#333333">
  2024年公司营收同比<tspan fill="#1A73E8" font-weight="bold">增长35%</tspan>达到<tspan fill="#1A73E8" font-weight="bold">12亿元</tspan>创历史新高
</text>
```

### 元素分组（强制）

将逻辑相关元素包裹在顶层 `<g id="...">` 组内。在 PPTX 中生成 PowerPoint 组，便于选择/移动/编辑，并为可选的逐元素入场动画提供稳定锚点。

> ⚠️ 仅 `<g opacity="...">` 被禁止（§2）。用于分组的普通 `<g>` 是必须的。

**动画就绪规则**：`<svg>` 的直接子元素应是语义组，非原始绘制原子。目标为每页 **3–8 个顶层内容 `<g id>` 组**（3–8 预算不含页面 chrome——见下方）；每个内容组在所选 `--animation-trigger` 模式下成为一个入场步骤（`on-click` 为每次点击一步，`after-previous` 为级联，`with-previous` 为同时播放）。

**Chrome 组自动排除。** 导出器将 id 含 chrome 标记的顶层组视为页面装饰并跳过动画序列——它们随幻灯片一起出现。标记（对 id 按 `-` / `_` 分割后匹配）：`background`、`bg`、`decoration` / `decorations` / `decor`、`header`、`footer`、`chrome`、`watermark`、`pagenumber` / `pagenum` / `page-number`。因此 `<g id="bg-texture">`、`<g id="cover-footer">`、`<g id="p03-header">`、`<g id="bottom-decor">` 都跳过动画同时保留 `<g>` 包装器用于编辑/分组。Chrome 使用这些命名约定——**不要**去掉 `<g>` 包装器。

**分组内容**：

| 分组单元      | 包含                                                                            |
| ------------- | ------------------------------------------------------------------------------- |
| 卡片 / 面板   | 背景矩形 + （仅当悬浮在照片/有色面板上时的可选阴影——见 §6）+ 图标 + 标题 + 正文 |
| 流程步骤      | 数字圆 + 图标 + 标签 + 描述                                                     |
| 列表项        | 项目符号/数字 + 图标 + 标题 + 描述                                              |
| 图标-文字组合 | 图标元素 + 相邻标签                                                             |
| 页头          | 标题 + 副标题 + 强调装饰                                                        |
| 页脚          | 页码 + 品牌标识                                                                 |
| 装饰集群      | 相关装饰形状（圆环、球体、圆点）                                                |

**不要**：

- 把整页放进一个巨大 `<g>`；那只有一个动画步骤。
- 留下大量未分组的顶层 `<rect>` / `<text>` / `<path>` 元素；后备动画上限为 8 个图元，密集平面页可能跳过动画。
- 将每个图标、文字行或装饰标记拆为独立顶层组；那创造过多点击步骤。
- 使用匿名顶层组。每个顶层语义组需要描述性 `id`。

**示例**：

```xml
<g id="card-benefits-1">
  <!-- 此卡片悬浮在有色面板上——阴影适当。在纯白画布上省略 filter。 -->
  <rect x="60" y="115" width="565" height="260" rx="20" fill="#FFFFFF" filter="url(#shadow)"/>
  <use data-icon="chunk/bolt" x="108" y="163" width="44" height="44" fill="#0071E3"/>
  <text x="105" y="270" font-size="56" font-weight="bold" fill="#0071E3">10×</text>
  <text x="250" y="270" font-size="30" font-weight="bold" fill="#1D1D1F">Faster</text>
  <text x="105" y="310" font-size="18" fill="#6E6E73">Reduce production time from days to hours.</text>
</g>
```

**命名**：顶层 `<g>` 上的描述性 `id` 是**必须的**（如 `card-1`、`step-discover`、`header`、`footer`）。每个顶层 `<g id>` 成为 PPTX 导出中逐元素入场动画的一个锚点；缺少时，导出器回退到至多 8 个顶层图元或在密集页跳过动画。

---

## 5. 后处理管线（3 步）

必须按顺序执行——跳过或添加额外标志是禁止的：

```bash
# 1. 拆分演讲备注为逐页备注文件
python3 scripts/total_md_split.py <project_path>

# 2. SVG 后处理（图标嵌入、图片裁剪/嵌入、文本扁平化、圆角矩形转路径）
python3 scripts/finalize_svg.py <project_path>

# 3. 导出 PPTX（从 svg_final/，默认嵌入演讲备注）
python3 scripts/svg_to_pptx.py <project_path>
# 输出：
#   exports/<project_name>_<timestamp>.pptx           ← 主原生 pptx
#   backup/<timestamp>/<project_name>_svg.pptx        ← SVG 快照
#   backup/<timestamp>/svg_output/                    ← 执行器 SVG 源备份
```

**可选动画标志**（仅用户要求时）：
- `-t <effect>` — 页面过渡（`fade` / `push` / `wipe` / `split` / `strips` / `cover` / `random` / `none`；默认 `fade`）
- `-a <effect>` — 逐元素入场动画（`fade` / `mixed` / `random` / 22 种命名效果之一 / `none`；默认 `mixed`）。锚定在顶层 `<g id="...">` 组。
- `--animation-trigger {on-click,with-previous,after-previous}` — 启动模式匹配 PowerPoint 动画窗格的"开始"下拉。默认 `after-previous`（幻灯片入场时级联；通过 `--animation-stagger <seconds>` 控制节奏）；`on-click` 逐点击推进；`with-previous` 所有组同时播放。
- `--animation-config <path>` — 可选对象级动画 sidecar。默认：`<project>/animations.json`（存在时）。
- `--auto-advance <seconds>` — 展台式自动播放

**可选录制旁白**（仅用户要求有声/视频导出时）：

```bash
python3 scripts/notes_to_audio.py <project_path> --voice zh-CN-XiaoxiaoNeural
python3 scripts/svg_to_pptx.py <project_path> --recorded-narration audio
```

- `notes_to_audio.py` 读取拆分后的 `notes/*.md` 文件，为每页写入一个音频文件到 `audio/`。默认 `edge` 输出为 MP3；配置的云提供商可能根据设置输出 MP3 或 WAV。
- `--recorded-narration audio` 准备 PowerPoint 的录制计时和旁白：每页需匹配 `m4a` / `mp3` / `wav` 音频，每段时长须可被 `ffprobe` 读取，且 `on-click` 对象动画被拒绝。
- `--recorded-narration audio` 嵌入匹配音频、保留演讲备注、并从音频时长设置幻灯片计时。
- `--narration-audio-dir audio` 是部分音频覆盖的底层嵌入路径；它不准备完整的录制计时导出。
- 不支持长音频导入和自动长音频拆分。

完整参考：[`animations.md`](animations.md)。

**禁止**：
- 不得用 `cp` 替代 `finalize_svg.py`
- 不得强制 `-s output` 用于旧版/预览 pptx（PowerPoint 内部 SVG 解析器丢失图标和圆角）。默认自动分割已为原生版提供高保真源，无需影响旧版。
- 不得使用 `--only`（它会抑制两个输出文件之一）

> 源目录分割：默认 `svg_to_pptx.py` 为原生 pptx 读取 `svg_output/`（保留图标 `<use>`、图片 `preserveAspectRatio` → `srcRect`、圆角矩形 `rx/ry` → `prstGeom roundRect`），为旧版/预览 pptx 读取 `svg_final/`（PowerPoint 内部 SVG 解析器需要扁平化形式）。仅当你特别需要两个产品都从单一来源读取时传入 `-s output` 或 `-s final`。

**重跑规则**：后处理后对 `svg_output/` 的任何修改需重新运行步骤 2-3。步骤 1 仅在 `notes/total.md` 变化时重跑。

---

## 6. 阴影与叠加技法

> `<mask>` 元素和 `<image opacity="...">` 被禁止。始终使用堆叠 `<rect>` 或渐变叠加替代（见 §2）。

### 阴影

> **阴影是克制，非默认。** "设计感"来自缺失，非丰富。

#### 何时使用

仅当元素确实悬浮在另一层之上时：
- 照片或有色面板上的卡片/引言气泡/注释
- 从同级中突出的单个主 CTA 或"推荐"项
- 叠加层（标注、工具提示、模态强调）
- 纹理背景上的悬浮图片卡片

#### 何时不使用

- 背景面板/分割线/装饰条 — 它们是地板
- 2/3/4 宫格中的同级卡片 — 全部保持平坦
- 有可见边框、渐变填充或强色调的容器 — 多余
- 正文段落容器 — 破坏扫读节奏
- 装饰线/分割线/图标 — 它们是符号，非物体
- 只有一个内容容器的页面 — 无第二层可提升
- 深色背景 — 黑色阴影消失；用 1px 低透明度白色描边或外发光

**每页预算**：≤2-3 个带阴影元素。若要用第 4 个，先去掉一个。

#### 每页单一光源

页面上所有 `feOffset` 必须共享相同的 `dx`/`dy` 方向。默认：`dx="0"`，`dy="4"` 至 `dy="8"`（光从上前方来）。

#### 克制优于可见

标准："阴影是被感觉到的，而非被看到的。"若被注意到，就太强了。
- 静止卡片：`flood-opacity` 0.06-0.12
- 提升元素（CTA、叠加层）：最大 `flood-opacity` 0.20
- 超过 0.20 = Office 2007 硬阴影感
- 颜色：低透明度的近黑色，或背景的较深色调。品牌色阴影仅用于共享该色相的强调元素。

#### 最多两级提升

每页最多有两个非地板层级。

| 层级           | 何时                                 | dy   | stdDeviation | flood-opacity |
| -------------- | ------------------------------------ | ---- | ------------ | ------------- |
| 地板（无阴影） | 背景、同级网格卡片、分割线、正文容器 | —    | —            | —             |
| 静止           | 照片/面板上的卡片、次要标注          | 2-4  | 4-8          | 0.06-0.10     |
| 提升           | 主 CTA、聚焦/推荐卡片、叠加层        | 6-10 | 10-16        | 0.12-0.20     |

#### 不要堆叠视觉重量工具

每个容器选**一个**：阴影、边框、渐变填充或强色调。堆叠 = 即时模板感。

---

#### Filter 柔阴影 — 推荐

最适合：卡片、悬浮面板、提升元素。`svg_to_pptx` 转换器自动将 `feGaussianBlur` + `feOffset` 转换为原生 PPTX `<a:outerShdw>`。

```xml
<defs>
  <filter id="softShadow" x="-15%" y="-15%" width="140%" height="140%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="12"/>
    <feOffset dx="0" dy="6" result="offsetBlur"/>
    <feFlood flood-color="#000000" flood-opacity="0.10" result="shadowColor"/>
    <feComposite in="shadowColor" in2="offsetBlur" operator="in" result="shadow"/>
    <feMerge>
      <feMergeNode in="shadow"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>
<rect x="60" y="60" width="400" height="240" rx="12" fill="#FFFFFF" filter="url(#softShadow)"/>
```

推荐参数（层级指导见上方"最多两级提升"）：
```
stdDeviation:   4–16       （静止卡片：4–8；提升元素：10–16）
flood-opacity:  0.06–0.12  （静止卡片——默认）
                0.12–0.20  （仅提升元素——主 CTA、叠加层）
                绝不      > 0.20（Office 2007 硬阴影感）
dy:             2–10       （静止：2–4；提升：6–10）
dx:             0–2        （须与页面上其他阴影方向一致——单一光源）
```

#### 彩色阴影

最适合：强调按钮、品牌色卡片。用元素自身色族替代黑色。

```xml
<filter id="colorShadow" x="-15%" y="-15%" width="140%" height="140%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="10"/>
  <feOffset dx="0" dy="6" result="offsetBlur"/>
  <feFlood flood-color="#1A73E8" flood-opacity="0.20" result="shadowColor"/>
  <feComposite in="shadowColor" in2="offsetBlur" operator="in" result="shadow"/>
  <feMerge>
    <feMergeNode in="shadow"/>
    <feMergeNode in="SourceGraphic"/>
  </feMerge>
</filter>
```

将 `flood-color` 替换为元素的品牌色。保持 `flood-opacity` 0.12-0.20。仅保留给每页单个主 CTA——对每个按钮都用则失去提示作用。

#### 发光效果

最适合：标题高亮、关键指标、主视觉文字。转换器自动将不含 `feOffset` 的 `feGaussianBlur` 转换为原生 PPTX `<a:glow>`。

```xml
<defs>
  <filter id="titleGlow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="6" result="blur"/>
    <feFlood flood-color="#1A73E8" flood-opacity="0.45" result="glowColor"/>
    <feComposite in="glowColor" in2="blur" operator="in" result="glow"/>
    <feMerge>
      <feMergeNode in="glow"/>
      <feMergeNode in="SourceGraphic"/>
    </feMerge>
  </filter>
</defs>
<text x="640" y="360" text-anchor="middle" font-size="48" fill="#1A73E8" filter="url(#titleGlow)">Key Insight</text>
```

推荐参数：
```
stdDeviation:   4–8      （小=微妙，大=突出）
flood-color:    品牌色或强调色（非黑色）
flood-opacity:  0.35–0.55  （比阴影更强以确保可见）
```

**vs 阴影**：无 `<feOffset>`（或 dx=0/dy=0）。转换器以此区分发光和阴影。

#### 分层矩形阴影 — 高兼容性兜底

最适合：与旧版 PowerPoint 的最大兼容性。在主卡片后堆叠 2-3 个半透明矩形：

```xml
<!-- 阴影层（从后到前，最大偏移在最后） -->
<rect x="68" y="72" width="400" height="240" rx="16" fill="#000000" fill-opacity="0.03"/>
<rect x="65" y="69" width="400" height="240" rx="14" fill="#000000" fill-opacity="0.05"/>
<rect x="62" y="66" width="400" height="240" rx="12" fill="#1A73E8" fill-opacity="0.04"/>
<!-- 主卡片 -->
<rect x="60" y="60" width="400" height="240" rx="12" fill="#FFFFFF"/>
```

### 图片叠加

#### 线性渐变叠加 — 最常用

最适合：图文页。渐变方向应匹配文字位置（文字在左 → 渐变向左加深）。

```xml
<image href="..." x="0" y="0" width="1280" height="720" preserveAspectRatio="xMidYMid slice"/>
<defs>
  <linearGradient id="imgOverlay" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#1A1A2E" stop-opacity="0.85"/>
    <stop offset="55%"  stop-color="#1A1A2E" stop-opacity="0.30"/>
    <stop offset="100%" stop-color="#1A1A2E" stop-opacity="0"/>
  </linearGradient>
</defs>
<rect x="0" y="0" width="1280" height="720" fill="url(#imgOverlay)"/>
```

#### 底部渐变条

最适合：封面和全图页面底部标题。

```xml
<defs>
  <linearGradient id="bottomBar" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%"   stop-color="#000000" stop-opacity="0"/>
    <stop offset="100%" stop-color="#000000" stop-opacity="0.72"/>
  </linearGradient>
</defs>
<rect x="0" y="380" width="1280" height="340" fill="url(#bottomBar)"/>
```

#### 径向渐变叠加 — 暗角效果

最适合：全屏氛围页；将注意力引向中心。

```xml
<defs>
  <radialGradient id="vignette" cx="50%" cy="50%" r="70%">
    <stop offset="0%"   stop-color="#000000" stop-opacity="0"/>
    <stop offset="100%" stop-color="#000000" stop-opacity="0.58"/>
  </radialGradient>
</defs>
<rect x="0" y="0" width="1280" height="720" fill="url(#vignette)"/>
```

#### 品牌色叠加

最适合：需要强视觉品牌识别的幻灯片。

```xml
<defs>
  <linearGradient id="brandOverlay" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%"   stop-color="#005587" stop-opacity="0.80"/>
    <stop offset="100%" stop-color="#005587" stop-opacity="0.10"/>
  </linearGradient>
</defs>
<rect x="0" y="0" width="1280" height="720" fill="url(#brandOverlay)"/>
```

### 速查表

| 场景                                       | 推荐技法                                             | 避免                         |
| ------------------------------------------ | ---------------------------------------------------- | ---------------------------- |
| 卡片/面板阴影（仅悬浮在照片/有色面板上时） | Filter 柔阴影（`flood-opacity` 0.06–0.12，单一光源） | 硬黑阴影、全页泛滥           |
| 网格中的同级卡片                           | 全部平坦（无阴影）                                   | 均匀提升每张卡片             |
| 页面分区背景面板                           | 平坦填充，无阴影                                     | 将面板视为悬浮卡片           |
| 强调/CTA 按钮（每页一个）                  | 彩色阴影（同色族，`flood-opacity` 0.12–0.20）        | 通用灰色阴影、对每个按钮都用 |
| 标题/指标高亮                              | 发光 filter（品牌色，无偏移）                        | 正文过度使用                 |
| 图片上的文字                               | 线性渐变叠加（方向匹配文字侧）                       | 整图均匀平坦透明度           |
| 封面/全图页                                | 底部渐变条 + 品牌色                                  | 纯黑叠加                     |
| 氛围/主视觉页                              | 径向暗角                                             | 未处理的原始图片             |
| 需要最大 PPT 兼容性                        | 分层矩形阴影                                         | Filter 阴影                  |

---

## 7. 描边、文字与形状效果

### stroke-dasharray — 虚线/点线

转换为原生 PPTX `<a:prstDash>`。使用预设模式以获得最佳效果：

| SVG 值    | PPTX 预设     | 最适合                 |
| --------- | ------------- | ---------------------- |
| `4,4`     | Dash          | 通用虚线、分隔线       |
| `2,2`     | Dot (sysDot)  | 微妙点线边框、占位轮廓 |
| `8,4`     | Long dash     | 时间线连接器、流程箭头 |
| `8,4,2,4` | Long dash-dot | 技术图纸、标注线       |

```xml
<rect x="60" y="60" width="400" height="240" rx="12"
  fill="none" stroke="#999999" stroke-width="2" stroke-dasharray="4,4"/>

<line x1="100" y1="360" x2="1180" y2="360"
  stroke="#CCCCCC" stroke-width="1" stroke-dasharray="2,2"/>
```

### stroke-linejoin

控制线段在拐角处的连接方式。支持的值转换为原生 PPTX 线连接类型：

| SVG 值  | PPTX 等效        | 最适合                 |
| ------- | ---------------- | ---------------------- |
| `round` | 圆角连接         | 平滑折线图表、有机形状 |
| `bevel` | 斜角连接         | 技术图表               |
| `miter` | 尖角连接（默认） | 锐角矩形、箭头         |

```xml
<polyline points="100,200 200,100 300,200" fill="none"
  stroke="#1A73E8" stroke-width="3" stroke-linejoin="round"/>
```

### text-decoration

支持的文字装饰转换为原生 PPTX 文字格式：

| SVG 值         | PPTX 等效 | 最适合               |
| -------------- | --------- | -------------------- |
| `underline`    | 单下划线  | 强调、链接、关键术语 |
| `line-through` | 删除线    | 已移除项、前后对比   |

```xml
<text x="100" y="200" font-size="20" fill="#333333" text-decoration="underline">Important Term</text>

<!-- 逐 tspan 装饰 -->
<text x="100" y="240" font-size="18" fill="#333333">
  Regular text <tspan text-decoration="line-through" fill="#999999">old value</tspan> new value
</text>
```

### 渐变填充 — linearGradient 与 radialGradient

`<defs>` 中定义并通过 `fill="url(#id)"` 引用的渐变转换为原生 PPTX `<a:gradFill>`。可用作形状填充（不仅是叠加层），呈现精致表面。

**线性渐变** — 最适合按钮、头部条、背景面板：

```xml
<defs>
  <linearGradient id="btnGrad" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0%" stop-color="#1A73E8"/>
    <stop offset="100%" stop-color="#0D47A1"/>
  </linearGradient>
</defs>
<rect x="540" y="600" width="200" height="48" rx="24" fill="url(#btnGrad)"/>
```

**径向渐变** — 最适合聚光背景、圆形强调：

```xml
<defs>
  <radialGradient id="spotBg" cx="50%" cy="50%" r="70%">
    <stop offset="0%" stop-color="#1A73E8" stop-opacity="0.15"/>
    <stop offset="100%" stop-color="#1A73E8" stop-opacity="0"/>
  </radialGradient>
</defs>
<circle cx="640" cy="360" r="300" fill="url(#spotBg)"/>
```

### transform: rotate — 元素旋转

旋转转换为原生 PPTX `<a:xfrm rot="...">`。支持所有元素类型：`rect`、`circle`、`ellipse`、`line`、`path`、`polygon`、`polyline`、`image` 和 `text`。

```xml
<!-- 旋转装饰元素 -->
<rect x="100" y="100" width="60" height="60" fill="#1A73E8" fill-opacity="0.1"
  transform="rotate(45, 130, 130)"/>

<!-- 旋转文字标签 -->
<text x="50" y="400" font-size="14" fill="#999999"
  transform="rotate(-90, 50, 400)">Y-Axis Label</text>
```

**语法**：`rotate(angle)` 或 `rotate(angle, cx, cy)`，其中 `cx,cy` 为旋转中心。正角度顺时针旋转。

### 弧线路径 — 环形/饼图

用三角函数精确计算弧线端点坐标。不要估算——小误差产生严重错误形状。

**计算公式**（圆心 `cx,cy`，半径 `r`，角度 `θ` 单位为度）：
```
x = cx + r × cos(θ × π / 180)
y = cy + r × sin(θ × π / 180)
```

**关键规则**：
1. 从 **-90°**（12 点钟位置）开始，顺时针旋转
2. 每个扇区跨越 `百分比 × 360°`
3. 扇区 > 180° 时使用 **large-arc flag = 1**，否则 **0**
4. sweep-direction = 1（顺时针）用于外弧，0（逆时针）用于内弧返回
5. **始终验证**所有扇区角度之和等于 360°，且最后一个扇区的终点匹配第一个扇区的起点

**示例 — 75% 环形扇区**（圆心 400,400，外半径 180，内半径 100）：
```
起始角：-90°    → outer(400, 220), inner(400, 300)
结束角：-90+270=180° → outer(220, 400), inner(300, 400)
Large-arc flag：1（270° > 180°）

<path d="M 400,220 A 180,180 0 1,1 220,400 L 300,400 A 100,100 0 1,0 400,300 Z"/>
```

### 对角线上的多边形箭头

> 连接线优先使用 `marker-end`/`marker-start`（§1.1）。块状/宽实心/非连接箭头使用独立 polygon 或 path。

水平/垂直线可用简单点偏移作 `<polygon>` 箭头。对角线需要将三角形顶点旋转以匹配线段方向。

**方法** — 使用线段方向向量计算三角形顶点：

```
给定线段从 (x1,y1) 到 (x2,y2)：
1. 方向向量：dx = x2-x1, dy = y2-y1
2. 归一化：len = √(dx²+dy²), ux = dx/len, uy = dy/len
3. 垂线：px = -uy, py = ux
4. 箭头尖端 = (x2, y2)
5. 后点 1 = (x2 - ux×12 + px×5,  y2 - uy×12 + py×5)
6. 后点 2 = (x2 - ux×12 - px×5,  y2 - uy×12 - py×5)
```

**示例 — 对角线**从 (260,310) 到 (370,430)：
```
dx=110, dy=120, len≈162.8, ux=0.676, uy=0.737
px=-0.737, py=0.676
尖端：(370, 430)
后点1：(370-8.1-3.7, 430-8.8+3.4) = (358.2, 424.6)
后点2：(370-8.1+3.7, 430-8.8-3.4) = (365.6, 417.8)

<polygon points="370,430 365.6,417.8 358.2,424.6" fill="#C8A96E"/>
```

⚠️ 不要在对角线上使用固定的向下/向右三角形——箭头方向会错误。

---

## 8. 项目目录结构

```
project/
├── svg_output/    # 原始 SVG（执行器输出，含占位符）
├── svg_final/     # 后处理后最终 SVG（finalize_svg.py 输出）
├── images/        # 图片素材（用户提供）
├── notes/         # 演讲备注（.md 文件，匹配 SVG 名称）
│   └── total.md   # 完整演讲备注文档（拆分前）
├── templates/     # 项目模板（如有）
└── *.pptx         # 导出的 PPT 文件
```
