---
name: Word Master
description: >
  Word 文档创建、编辑与分析技能。支持使用 docx-js 创建新文档、
  通过 unpack/edit/repack 流程编辑已有文档、格式转换与内容提取。
  当用户要求"创建 Word 文档"、"编辑 docx"、"生成报告"、"制作合同"、
  "create Word document"、"edit docx" 时使用此技能。
---

# Word Master 技能

> Word 文档创建、编辑与分析。支持创建新文档（docx-js）、编辑已有文档（unpack → edit XML → repack）、格式转换（LibreOffice）。

## 概览

一个 .docx 文件是一个包含 XML 文件的 ZIP 压缩包。

## 快速参考

| 任务          | 方法                                                |
| ------------- | --------------------------------------------------- |
| 读取/分析内容 | `pandoc` 或 unpack 查看原始 XML                     |
| 创建新文档    | 使用 `docx-js` — 参见下方"创建新文档"               |
| 编辑已有文档  | Unpack → 编辑 XML → Repack — 参见下方"编辑已有文档" |
| 格式转换      | `docx_convert` 工具（LibreOffice）                  |

## 工具索引

| 工具名                       | 功能                                                      |
| ---------------------------- | --------------------------------------------------------- |
| `docx_project_init`          | 初始化项目目录（sources/scripts/output/unpacked/exports） |
| `docx_write_file`            | 在项目目录中写入文件                                      |
| `docx_read_file`             | 读取项目目录中的文件（支持行号范围）                      |
| `docx_generate`              | 使用 docx-js (Node.js) 生成 .docx 文件（需手写完整 JS）   |
| `docx_generate_from_outline` | **【推荐】**从结构化大纲生成 .docx，无需写 JS 代码        |
| `docx_unpack`                | 解压 .docx 为 XML 目录                                    |
| `docx_pack`                  | 将 XML 目录重新打包为 .docx                               |
| `docx_validate`              | 验证 .docx 文件结构                                       |
| `docx_accept_changes`        | 接受所有修订标记                                          |
| `docx_add_comment`           | 向 unpacked 文档添加注释                                  |
| `docx_convert`               | 格式转换（.doc → .docx、.docx → .pdf 等）                 |
| `docx_save_state`            | 保存工作流状态到 session                                  |

## 工作流程（4 大 Phase）

Word Master 的完整工作流按 4 大 Phase 组织，与 PPT Master 对齐：

| Phase             | 职责                                        | 主要工具                                     |
| ----------------- | ------------------------------------------- | -------------------------------------------- |
| **Phase 1: 准备** | 项目初始化 + 素材整理 + source_data.md      | `docx_project_init` + `docx_write_file`      |
| **Phase 2: 大纲** | 生成文档结构大纲 + 用户确认（outline_card） | `outline_card` + `final_answer(is_blocking)` |
| **Phase 3: 生成** | 基于已确认大纲生成 .docx                    | `docx_generate_from_outline`                 |
| **Phase 4: 下载** | 验证 + 下载链接卡片                         | `docx_validate` + `create_download_url`      |

> 当作为 material_maker agent 的子流程被调用时，Phase 1–2 由 material_flow skill 编排，
> Word Master 主要负责 Phase 3（生成）和 Phase 4（下载）的执行。

---

## Phase 1: 准备

> 项目初始化 + 素材整理。所有准备工作（目录创建、素材收集、source_data.md 写入）都在此 Phase 完成。

---

## 创建新文档

使用 JavaScript + docx-js 生成 .docx 文件，然后验证。

### 工作流（推荐：结构化大纲方式）

#### Phase 1: 准备
1. 使用 `docx_project_init` 初始化项目
2. 使用 `docx_write_file` 写入 `sources/source_data.md`（整理好的素材）

#### Phase 2: 大纲
3. 基于 source_data.md 生成结构化大纲，调用 `outline_card` 展示可编辑大纲卡片
4. ⛔ BLOCKING 等待用户确认大纲

#### Phase 3: 生成
5. 使用 `docx_generate_from_outline` 传入已确认大纲，自动生成企业级 .docx

#### Phase 4: 下载
6. 使用 `docx_validate` 验证文档结构
7. 使用 `create_download_url` 生成下载链接

```json
// 示例调用
docx_generate_from_outline({
  "title": "平安养老保险企业年金市场分析报告",
  "sections": [
    {
      "heading": "一、市场概况",
      "level": 1,
      "content": "截至2025年...市场规模达到0000亿元。\n竞争格局方面...",
      "subsections": [
        {
          "heading": "（一）受托管理业务",
          "level": 2,
          "content": "基于《全国企业年金基金业务数据摘要》进行分析：",
          "tables": [{
            "headers": ["年份", "企业数（个）", "职工数（人）", "受托管理资产（亿元）", "市场排名"],
            "rows": [
              ["2023", "27,750", "5,206,885", "4,744.6", "第2位"],
              ["2024", "26,978", "5,132,268", "5,290.7", "第2位"],
              ["2025", "27,443", "5,244,721", "6,182.5", "第2位"]
            ]
          }],
          "bullets": ["受托资产规模连续三年稳步增长", "**市场份额稳居行业第二**"]
        }
      ]
    }
  ],
  "output_filename": "report.docx"
})
```

自动套用平安养老险企业级样式（微软雅黑、小四正文、小二标题、目录、页眉页脚）。

### 工作流（备选：手写 JS 方式）

1. 使用 `docx_project_init` 初始化项目
2. 使用 `docx_write_file` 将 JS 代码写入 scripts/ 目录
3. 使用 `docx_generate` 执行 JS 生成 .docx
4. 生成工具自动调用 `docx_validate` 验证

> ⚠️ 注意：手写 JS 方式要求将完整 JS 代码放在 tool_call 参数中，当文档内容较长时可能导致截断失败。建议优先使用结构化大纲方式。

### Setup（JS 代码模板）

```javascript
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
        Header, Footer, AlignmentType, PageOrientation, LevelFormat, ExternalHyperlink,
        InternalHyperlink, Bookmark, FootnoteReferenceRun, PositionalTab,
        PositionalTabAlignment, PositionalTabRelativeTo, PositionalTabLeader,
        TabStopType, TabStopPosition, Column, SectionType,
        TableOfContents, HeadingLevel, BorderStyle, WidthType, ShadingType,
        VerticalAlign, PageNumber, PageBreak } = require('docx');

const doc = new Document({ sections: [{ children: [/* content */] }] });
Packer.toBuffer(doc).then(buffer => fs.writeFileSync("doc.docx", buffer));
```

### 页面尺寸

```javascript
// 重要：docx-js 默认 A4，非 US Letter
// 始终显式设置页面尺寸
sections: [{
  properties: {
    page: {
      size: {
        width: 12240,   // 8.5 inches (DXA)
        height: 15840   // 11 inches (DXA)
      },
      margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
    }
  },
  children: [/* content */]
}]
```

**常用页面尺寸（DXA 单位，1440 DXA = 1 inch）：**

| 纸张       | 宽度   | 高度   | 内容宽度（1" 边距） |
| ---------- | ------ | ------ | ------------------- |
| US Letter  | 12,240 | 15,840 | 9,360               |
| A4（默认） | 11,906 | 16,838 | 9,026               |

### 样式（默认：平安养老险企业级）

**所有新建 .docx 必须默认采用「平安养老险企业级样式」，除非用户明确要求其他样式。** 见下方「平安养老险企业级文档样式」章节，包含字体/字号/标题层级/目录/页眉页脚的完整模板。

---

## 平安养老险企业级文档样式（强制默认）

这是 Word Master 生成所有正式材料的唯一默认样式，对应中国平安养老保险股份有限公司的企业公文规范。

### 规范要点（不可省略）

| 项目       | 规范                                                                                  |
| ---------- | ------------------------------------------------------------------------------------- |
| 字体       | **微软雅黑**（中英文统一，eastAsia 与 ascii 均设为 Microsoft YaHei）                  |
| 字色       | **全黑** `#000000`，正文与所有标题均为黑色，禁用彩色字体                              |
| 正文字号   | **小四（12pt → size: 24）**，行距 1.5 倍                                              |
| 一级标题   | **小二（18pt → size: 36）**，加粗，编号 `一、二、三、…`                               |
| 二级标题   | **小三（15pt → size: 30）**，加粗，编号 `（一）（二）（三）…`                         |
| 三级标题   | **小四（12pt → size: 24）**，加粗，编号 `1. 2. 3. …`                                  |
| 文档大标题 | **小初（36pt → size: 72）**，加粗，居中，置于目录之前                                 |
| 目录       | **必须包含**，使用 `TableOfContents` + `features: { updateFields: true }`，覆盖 H1–H3 |
| 页眉       | 居右：`中国平安养老保险股份有限公司`                                                  |
| 页脚       | 居右：`第 X 页 / 共 Y 页`                                                             |
| 页面       | A4 纵向（11906 × 16838 DXA），上下左右边距均为 1 英寸（1440 DXA）                     |

### 完整模板（可直接套用，仅替换业务内容）

```javascript
const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, Header, Footer, PageNumber,
  AlignmentType, HeadingLevel, LevelFormat, PageBreak,
} = require('docx');

// 平安养老险企业级样式常量
const FONT = "Microsoft YaHei"; // 微软雅黑
const BLACK = "000000";
const SIZE_BODY     = 24; // 小四 12pt
const SIZE_H1       = 36; // 小二 18pt
const SIZE_H2       = 30; // 小三 15pt
const SIZE_H3       = 24; // 小四 12pt（加粗）
const SIZE_TITLE    = 72; // 小初 36pt（封面/文档大标题）

const doc = new Document({
  creator: "中国平安养老保险股份有限公司",
  features: { updateFields: true },  // 关键：打开时自动更新目录
  styles: {
    default: {
      document: {
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT, cs: FONT }, size: SIZE_BODY, color: BLACK },
        paragraph: { spacing: { line: 360, lineRule: "auto" } }, // 1.5 倍行距
      },
    },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_H1, bold: true, color: BLACK },
        paragraph: { spacing: { before: 360, after: 240, line: 360, lineRule: "auto" }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_H2, bold: true, color: BLACK },
        paragraph: { spacing: { before: 280, after: 200, line: 360, lineRule: "auto" }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_H3, bold: true, color: BLACK },
        paragraph: { spacing: { before: 220, after: 160, line: 360, lineRule: "auto" }, outlineLevel: 2 } },
      { id: "DocTitle", name: "Doc Title", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_TITLE, bold: true, color: BLACK },
        paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 1200, after: 600, line: 480, lineRule: "auto" } } },
    ],
  },

  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },          // A4 纵向
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "中国平安养老保险股份有限公司", font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK })],
        })],
      }),
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [
            new TextRun({ text: "第 ", font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK }),
            new TextRun({ children: [PageNumber.CURRENT], font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK }),
            new TextRun({ text: " 页 / 共 ", font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK }),
            new TextRun({ children: [PageNumber.TOTAL_PAGES], font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK }),
            new TextRun({ text: " 页", font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK }),
          ],
        })],
      }),
    },
    children: [
      // 1) 文档大标题（封面 / 首页）
      new Paragraph({ style: "DocTitle", children: [new TextRun({ text: "<在此处填写文档标题>" })] }),
      new Paragraph({ children: [new PageBreak()] }),

      // 2) 目录（自动生成，Word 打开时填充）
      new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),

      // 3) 正文示例：一级 / 二级 / 三级 / 正文段落
      new Paragraph({ style: "Heading1", children: [new TextRun({ text: "一、<一级标题>" })] }),
      new Paragraph({ style: "Heading2", children: [new TextRun({ text: "（一）<二级标题>" })] }),
      new Paragraph({ style: "Heading3", children: [new TextRun({ text: "1. <三级标题>" })] }),
      new Paragraph({ children: [new TextRun({ text: "<正文小四，行距 1.5 倍，黑色字。>" })] }),
    ],
  }],
});

Packer.toBuffer(doc).then(buffer => fs.writeFileSync("doc.docx", buffer));
```

## 强制约束（生成时必须遵守）

- **字体**：所有 TextRun 的 `font` 必须显式声明为 `{ name, eastAsia, ascii, hAnsi }` 全为 `Microsoft YaHei`，否则中文会回退到宋体。
- **字色**：所有 run 的 `color` 必须为 `"000000"`，禁止主题色与彩色强调。
- **标题层级**：一律使用 `style: "Heading1" / "Heading2" / "Heading3"`，不要用自定义样式名，TOC 才能识别。
- **编号**：在标题文本前手动写入 `一、`、`（一）`、`1.` 等中文序号（不使用 numbering 自动编号，避免 docx-js 复杂度）。
- **目录**：使用 `new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-3" })` 生成目录域，配合 `features: { updateFields: true }` 在 Word 打开时自动填充。标题必须使用 `style: "Heading1"` 等内置样式（带 `outlineLevel`）才能被 TOC 识别。
- **页眉/页脚**：每个 section 都要带上 headers/footers，不要省略；页码使用 `PageNumber.CURRENT` 与 `PageNumber.TOTAL_PAGES`。
- **页面**：A4 纵向，上下左右 1 英寸边距，禁止改横向（除非用户显式要求）。
- **行距**：正文 1.5 倍（`line: 360, lineRule: "auto"`），不要使用单倍行距。
- **字符串引号**：含中文引号的文本建议使用反引号 `` ` `` 包裹，避免转义问题。

---

### 旧版通用样式（仅在用户显式拒绝企业级样式时使用）

```javascript
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 24 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial" },
        paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 28, bold: true, font: "Arial" },
        paragraph: { spacing: { before: 180, after: 180 }, outlineLevel: 1 } },
    ]
  },
  sections: [{ children: [/* ... */] }]
});
```

### 关键规则（docx-js）

- **显式设置页面尺寸** — docx-js 默认 A4
- **横向：传纵向尺寸** — docx-js 内部交换宽高
- **不要使用 `\n`** — 使用独立的 Paragraph 元素
- **不要使用 Unicode 符号** — 使用 `LevelFormat.BULLET` + numbering config
- **PageBreak 必须在 Paragraph 内**
- **ImageRun 必须指定 `type`**
- **表格使用 DXA 宽度** — 不要用 `WidthType.PERCENTAGE`
- **表格需要双重宽度** — `columnWidths` 数组 + cell `width`
- **使用 `ShadingType.CLEAR`** — 不要用 SOLID
- **TOC 要求 HeadingLevel** — 不要用自定义样式
- **覆盖内置样式** — 使用精确 ID："Heading1"、"Heading2"
- **包含 `outlineLevel`** — TOC 需要（H1=0、H2=1）

---

## 编辑已有文档

**严格按照 3 步流程执行。**

### Step 1: Unpack

使用 `docx_unpack` 工具解压 .docx 为 XML 目录。

### Step 2: 编辑 XML

使用 `docx_write_file` 和 `docx_read_file` 工具编辑 unpacked/word/ 下的文件。

**修订标记使用 "AI Assistant" 作为作者。**

**XML 中使用智能引号：**
| 实体       | 字符               |
| ---------- | ------------------ |
| `&#x2018;` | '（左单引号）      |
| `&#x2019;` | '（右单引号/撇号） |
| `&#x201C;` | "（左双引号）      |
| `&#x201D;` | "（右双引号）      |

**添加注释：** 使用 `docx_add_comment` 工具。

### Step 3: Pack

使用 `docx_pack` 工具将 XML 目录重新打包为 .docx。

### 常见 XML 模式

**插入文本：**
```xml
<w:ins w:id="1" w:author="AI Assistant" w:date="2025-01-01T00:00:00Z">
  <w:r><w:t>inserted text</w:t></w:r>
</w:ins>
```

**删除文本：**
```xml
<w:del w:id="2" w:author="AI Assistant" w:date="2025-01-01T00:00:00Z">
  <w:r><w:delText>deleted text</w:delText></w:r>
</w:del>
```

**注释标记（w:commentRangeStart/End 是 w:r 的兄弟节点）：**
```xml
<w:commentRangeStart w:id="0"/>
<w:r><w:t>commented text</w:t></w:r>
<w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
```

---

## 格式转换

使用 `docx_convert` 工具通过 LibreOffice 进行格式转换。

| 场景         | 用法                                                |
| ------------ | --------------------------------------------------- |
| .doc → .docx | `docx_convert(input_file="doc.doc", format="docx")` |
| .docx → .pdf | `docx_convert(input_file="doc.docx", format="pdf")` |

---
