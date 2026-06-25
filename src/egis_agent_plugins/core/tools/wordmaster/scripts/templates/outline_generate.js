const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Header, Footer, PageNumber,
  Table, TableRow, TableCell, BorderStyle, WidthType, ShadingType,
  AlignmentType, HeadingLevel, LevelFormat, PageBreak, TableOfContents,
} = require("docx");

const FONT = "Microsoft YaHei";
const BLACK = "000000";
const SIZE_BODY  = 24;
const SIZE_H1    = 36;
const SIZE_H2    = 30;
const SIZE_H3    = 24;
const SIZE_TITLE = 72;

const DOC_TITLE = __DOC_TITLE__;
const OUTPUT_FILENAME = __OUTPUT_FILENAME__;
const SECTIONS = __SECTIONS__;

// ── 辅助函数 ──

function makeHeading(text, level) {
  const styleMap = { 1: "Heading1", 2: "Heading2", 3: "Heading3" };
  return new Paragraph({
    style: styleMap[level] || "Heading1",
    children: [new TextRun({ text, font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: level === 1 ? SIZE_H1 : level === 2 ? SIZE_H2 : SIZE_H3, bold: true, color: BLACK })],
  });
}

// 支持简单内联加粗：**文本** → 加粗
function parseInline(text) {
  const parts = text.split(/(\*\*[^*]+\*\*)/);
  return parts.filter(Boolean).map(part => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return new TextRun({ text: part.slice(2, -2), font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_BODY, bold: true, color: BLACK });
    }
    return new TextRun({ text: part, font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT }, size: SIZE_BODY, color: BLACK });
  });
}

function makeBody(text) {
  return new Paragraph({
    children: parseInline(text),
    spacing: { line: 360, lineRule: "auto" },
  });
}

function makeBullet(text, level) {
  return new Paragraph({
    numbering: { reference: "bullets", level: level || 0 },
    children: parseInline(text),
    spacing: { line: 360, lineRule: "auto" },
  });
}

function makeTable(tableData) {
  const headers = tableData.headers || [];
  const rows = tableData.rows || [];
  const colCount = headers.length || (rows[0] && rows[0].length) || 1;
  const tableWidth = 9026; // A4 content width (11906 - 1440*2)
  const colWidth = Math.floor(tableWidth / colCount);
  const columnWidths = Array(colCount).fill(colWidth);
  const border = { style: BorderStyle.SINGLE, size: 1, color: "999999" };
  const borders = { top: border, bottom: border, left: border, right: border };

  function makeCell(text, isHeader) {
    return new TableCell({
      borders,
      width: { size: colWidth, type: WidthType.DXA },
      shading: isHeader ? { fill: "E8E8E8", type: ShadingType.CLEAR } : undefined,
      margins: { top: 60, bottom: 60, left: 100, right: 100 },
      children: [new Paragraph({
        children: [new TextRun({
          text: String(text || ""),
          font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT },
          size: SIZE_BODY,
          bold: isHeader,
          color: BLACK,
        })],
      })],
    });
  }

  const tableRows = [];
  if (headers.length > 0) {
    tableRows.push(new TableRow({ children: headers.map(h => makeCell(h, true)) }));
  }
  for (const row of rows) {
    tableRows.push(new TableRow({ children: row.map(c => makeCell(c, false)) }));
  }

  return new Table({
    width: { size: tableWidth, type: WidthType.DXA },
    columnWidths,
    rows: tableRows,
  });
}

// 兜底：识别 content 中的 Markdown 表格块（| a | b |\n|---|---|\n| 1 | 2 |）
function parseMarkdownTableBlock(lines, start) {
  if (start + 1 >= lines.length) return null;
  const headerLine = lines[start];
  const sepLine = lines[start + 1];
  if (!/^\s*\|.*\|\s*$/.test(headerLine)) return null;
  if (!/^\s*\|[\s:\-|]+\|\s*$/.test(sepLine)) return null;
  if (!/-/.test(sepLine)) return null;
  const splitRow = (s) => s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
  const headers = splitRow(headerLine);
  const rows = [];
  let i = start + 2;
  while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
    rows.push(splitRow(lines[i]));
    i++;
  }
  return { table: { headers, rows }, end: i };
}

function parseContentBlocks(text) {
  const lines = text.split("\n");
  const blocks = [];
  let buf = [];
  const flushText = () => {
    const joined = buf.filter(Boolean);
    if (joined.length) blocks.push({ type: "text", lines: joined });
    buf = [];
  };
  let i = 0;
  while (i < lines.length) {
    const tbl = parseMarkdownTableBlock(lines, i);
    if (tbl) {
      flushText();
      blocks.push({ type: "table", data: tbl.table });
      i = tbl.end;
    } else {
      buf.push(lines[i]);
      i++;
    }
  }
  flushText();
  return blocks;
}

function renderSection(sec) {
  const items = [];
  items.push(makeHeading(sec.heading, sec.level || 1));
  if (sec.content) {
    const blocks = parseContentBlocks(sec.content);
    for (const blk of blocks) {
      if (blk.type === "text") {
        for (const p of blk.lines) items.push(makeBody(p));
      } else if (blk.type === "table") {
        items.push(makeTable(blk.data));
        items.push(new Paragraph({ spacing: { after: 200 }, children: [] }));
      }
    }
  }
  if (sec.bullets) {
    for (const b of sec.bullets) {
      items.push(makeBullet(b, 0));
    }
  }
  if (sec.tables) {
    for (const t of sec.tables) {
      items.push(makeTable(t));
      items.push(new Paragraph({ spacing: { after: 200 }, children: [] }));
    }
  }
  if (sec.subsections) {
    for (const sub of sec.subsections) {
      items.push(...renderSection(sub));
    }
  }
  return items;
}

const bodyChildren = [];
for (const sec of SECTIONS) {
  bodyChildren.push(...renderSection(sec));
}

const doc = new Document({
  creator: "平安养老保险股份有限公司",
  features: { updateFields: true },
  numbering: {
    config: [{
      reference: "bullets",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "\u25E6", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
      ],
    }],
  },
  styles: {
    default: {
      document: {
        run: { font: { name: FONT, eastAsia: FONT, ascii: FONT, hAnsi: FONT, cs: FONT }, size: SIZE_BODY, color: BLACK },
        paragraph: { spacing: { line: 360, lineRule: "auto" } },
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
        paragraph: { alignment: "center", spacing: { before: 1200, after: 600, line: 480, lineRule: "auto" } } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
      },
    },
    headers: {
      default: new Header({
        children: [new Paragraph({
          alignment: AlignmentType.CENTER,
          children: [new TextRun({ text: "平安养老保险股份有限公司", font: { name: FONT, eastAsia: FONT }, size: 18, color: BLACK })],
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
      new Paragraph({ style: "DocTitle", children: [new TextRun({ text: DOC_TITLE })] }),
      new Paragraph({ children: [new PageBreak()] }),
      new TableOfContents("目录", { hyperlink: true, headingStyleRange: "1-3" }),
      new Paragraph({ children: [new PageBreak()] }),
      ...bodyChildren,
    ],
  }],
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync(OUTPUT_FILENAME, buffer);
  console.log("Generated: " + OUTPUT_FILENAME + " (" + buffer.length + " bytes)");
});
