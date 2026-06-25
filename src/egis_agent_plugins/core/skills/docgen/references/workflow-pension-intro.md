# 养老险优势介绍 Workflow

> docgen_pension_intro_flow 的公共执行指南。doc_creator_agent 私有 skill 优先级更高；本文件只保留公共能力侧约束。

## 调用序列

```text
docgen_pension_intro_flow(instance_id="default", action="start")
docgen_pension_intro_flow(instance_id="default", action="select_template")
final_answer(is_blocking=true, answer="")

# 用户选择综合版 / HR版 / 财务版后
docgen_pension_intro_flow(instance_id="default", action="generate", version="comprehensive")
docgen_pension_intro_flow(instance_id="default", action="show_docx")
final_answer(is_blocking=true, answer="")
```

## 版本映射

| 用户选择 | version |
| --- | --- |
| 综合版 | comprehensive |
| HR版 / 人事版 | hr |
| 财务版 | finance |

## V5 模板要求

模板来源：`公司介绍模板（V5版）.docx`。

- 公司优势介绍：综合版约 1000 字；HR/财务版约 500 字；来源为受托优势素材第一部分公司实力与 `1 平安养老险公司实力v5.docx`，重复内容必须合并。
- 投资管理能力：财务版约 500 字，其它版本约 300 字；来源 `2平安养老险投资管理能力V52.docx`。
- 投资风控能力：财务版约 500 字，其它版本约 300 字；来源 `平安养老险年金风控管理V5.docx`。
- 投资客户服务能力：HR/财务版约 500 字，综合版约 300 字；来源 `四、平安养老险年金投资客户服务v5.docx`。
- 受托资管能力：约 300 字；来源受托优势素材第二部分。
- 受托运营能力：约 300 字；来源受托优势素材第三部分。
- 受托风控能力：约 300 字；来源受托优势素材第四部分。
- 受托服务能力：HR/财务版约 500 字，综合版约 300 字；来源受托优势素材第五部分。
- 受托系统能力：HR/财务版约 500 字，综合版约 300 字；来源受托优势素材第六部分。

## 生成约束

`generate` 必须在 Flow 内部完成：

1. 创建并保存 V5 模板结构 artifact。
2. 按能力项限定知识库、标签和来源文件执行固定 RAG。
3. 调用 AI 将 RAG 证据总结为客户汇报成稿。
4. 校验每个能力项字数；超字数必须让 AI 重写压缩，仍不合格则失败，不允许机械截断正文。
5. 生成 Word 并通过 eigenpal/docx-editor 右侧抽屉展示。

如果 RAG 无有效素材、AI 总结失败或字数严重超限，应返回错误，不得用 evidence 清洗结果兜底成稿。
