---
name: RAG 检索技能
description: 需要从知识库查证、总结或回答文档相关问题时使用。调用一次 rag，由内部 workflow 自动完成检索、重排、深读和证据打包。
---

# RAG

需要知识库证据时，调用一次 `rag`：

```text
rag(query="用户原始问题", source="auto", filters={}, hints={}, max_retries=1)
```

不要手动拆成多个检索子步骤；检索编排由 workflow 负责。workflow 会先做
rewrite/analyze 和 route；进入 RAG 分支后，会先选文档，再在选中文档里召回
chunk，随后 rerank/MMR，并按文档大小决定通读或关联扩展。

如果用户已经在前端指定资料范围，系统会把完整的
`rag_state.rag_filter` 注入到 `filters.rag_filter`。不要自行把范围改写成
`kb_id`、`tag_ids`、`file_ids` 等扁平字段；层级关系由 RAG workflow 解析。

## RAG 策略判断

`hints` 是你对用户意图的语义判断，只作为系统策略的输入提示，最终执行由 workflow 决定。

- 用户明确提到文件名、报告名、PDF、附件、制度名，或“基于/根据 + 某类报告/摘要/材料/最近N年报告”时，`document_match_preference="filename"`。
- 用户没有明确文件名，只描述主题、趋势、优劣、原因、对比时，`document_match_preference="summary"`。
- 用户既提文件又问复杂主题时，`document_match_preference="balanced"`。
- 用户说“总结全文 / 通读 / 基于整篇 / 提炼全文”时，`read_preference="full_document"`。
- 用户问“哪一条 / 某个指标 / 定义 / 时间 / 金额 / 是否包含”时，`read_preference="related_chunks"`。
- 用户问“对比 / 趋势 / 优劣 / 原因分析 / 综合分析”时，`read_preference="mixed"`。
- 用户明确说“不要/排除/别用某个文件”时，必须重新调用 `rag`，并在 `hints.excluded_file_names` 中写入要排除的文件名；不能直接复用上一轮证据回答。

### Few-shot

用户：基于《企业年金业务数据摘要2023.pdf》，分析平安养老险的优劣势

调用：

```text
rag(
  query="基于《企业年金业务数据摘要2023.pdf》，分析平安养老险的优劣势",
  source="internal",
  hints={
    "document_match_preference": "filename",
    "read_preference": "full_document",
    "reason": "用户明确指定文件，并要求基于该文件做整体分析"
  }
)
```

用户：分析平安养老险 2021-2023 年企业年金业务趋势

调用：

```text
rag(
  query="分析平安养老险 2021-2023 年企业年金业务趋势",
  source="internal",
  hints={
    "document_match_preference": "summary",
    "read_preference": "mixed",
    "reason": "用户没有指定具体文件，问题是主题型趋势分析，需要按摘要和语义选择相关文档"
  }
)
```

用户：基于最近3年企业年金数据摘要报告，分析平安养老险优劣和趋势

调用：

```text
rag(
  query="基于最近3年企业年金数据摘要报告，分析平安养老险优劣和趋势",
  source="internal",
  hints={
    "document_match_preference": "filename",
    "read_preference": "mixed",
    "reason": "用户明确要求基于最近3年的数据摘要报告，核心是先定位对应年度的数据摘要报告文档，再在文档内分析平安养老险"
  }
)
```

用户：企业年金缴费比例上限是多少？

调用：

```text
rag(
  query="企业年金缴费比例上限是多少？",
  source="internal",
  hints={
    "document_match_preference": "summary",
    "read_preference": "related_chunks",
    "reason": "用户询问具体条款或指标，适合召回相关片段"
  }
)
```

用户：基于我选的资料，分析公司优势

调用：

```text
rag(
  query="基于我选的资料，分析公司优势",
  source="internal",
  hints={
    "document_match_preference": "summary",
    "read_preference": "mixed",
    "reason": "前端已指定资料范围，用户没有指定具体文件，需要在资料范围内按主题选择文档"
  }
)
```

用户：结合最新政策，分析企业年金业务未来趋势

调用：

```text
rag(
  query="结合最新政策，分析企业年金业务未来趋势",
  source="auto",
  hints={
    "document_match_preference": "summary",
    "read_preference": "mixed",
    "reason": "用户要求最新政策，可能需要 web；同时问题仍与知识库主题相关"
  }
)
```

用户：不要《平安养老险公司实力v5.docx》这个文件，重新写

调用：

```text
rag(
  query="不要《平安养老险公司实力v5.docx》这个文件，基于最近3年企业年金数据摘要报告，重新分析平安养老险优劣和趋势",
  source="internal",
  hints={
    "document_match_preference": "filename",
    "read_preference": "mixed",
    "excluded_file_names": ["平安养老险公司实力v5.docx"],
    "reason": "用户明确排除上一轮混入的公司实力文件，且仍要求基于最近3年数据摘要报告重新检索"
  }
)
```

不需要检索的纯问候、感谢、道别或普通续聊，直接调用 `final_answer`。

## 回答约束

- 只使用 `rag` 返回的证据包回答。
- 证据中没有的信息，明确说明未检索到；不要用常识或记忆补全。
- 新问题默认重新检索；只有用户明确说“继续、上述、刚才、基于上一轮”时，才可承接上一轮。
- 面向用户自然表达，不暴露内部工具链、chunk ID 以外的技术细节。
- 本轮结束必须调用 `final_answer`。
- 引用知识库内容时，在对应文字末尾用 `[N]` 标注证据编号（如 `[1]`、`[2]`），同一来源复用同一编号。禁止使用 `<kb>` 等其它引用格式。
