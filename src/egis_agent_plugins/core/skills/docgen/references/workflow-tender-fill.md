# DocGen 标书投标填充 Workflow（Step 4）

> docgen_tender_fill_flow 的完整执行指南。

---

## 前置条件

- Step 3 已完成，`template` artifact 已确认（用户已确认投标模板结构）
- session state 中有 `docgen_state.project_id` 和 `docgen_state.project_path`

---

## 步骤详解

### Step F1: start — 创建填充流程实例

```
docgen_tender_fill_flow(instance_id="default", action="start")
```

**Effect**: 读取已确认的模板 artifact，输出章节列表供 LLM 参考。

**输出**: 章节摘要（ID + 标题），提示 LLM 用 rag 逐章节检索。

---

### Step F2: fill — 准备 RAG 填充

```
docgen_tender_fill_flow(instance_id="default", action="fill")
```

**Effect**: 输出章节列表和 RAG 检索指引。

**LLM 操作序列**（在 Flow 外部执行）:
```
对每个章节:
  rag(query="章节标题 相关内容") → 获取填充内容
收集所有检索结果后，组装成 filled_data_json
```

---

### Step F3: receive_fill — 提交填充结果

```
docgen_tender_fill_flow(
  instance_id="default",
  action="receive_fill",
  args={"filled_data_json": "<完整JSON>"}
)
```

**filled_data_json 格式**:
```json
{
  "document_title": "投标材料",
  "sections": [
    {
      "id": "section_1",
      "level": 1,
      "title": "公司简介",
      "content": "（rag检索到的填充内容）",
      "rag_result": "（可选，rag原始结果）",
      "fill_strategy": "rag_search"
    },
    ...
  ]
}
```

**Effect**: 保存填充模板到 `templates/filled_template.json`，注册 artifact: key="filled_template"。

---

### Step F4: generate — 生成最终 Word

```
docgen_tender_fill_flow(instance_id="default", action="generate")
```

**Effect 内部**:
1. 读取 `filled_template` artifact
2. 调用 `template_to_markdown()` 转为 MD
3. 保存 MD 到 `drafts/final_tender.md`
4. 尝试 docx 生成，失败降级为 MD
5. 注册 artifact: key="final_word"

**输出**: Word 文档路径

---

### Step F5: show — 展示最终投标材料 ⛔ BLOCKING

```
docgen_tender_fill_flow(instance_id="default", action="show")
```

**on_render**: 渲染 docx-editor A2UI 卡片
- iframe 嵌入 docx-editor
- 展示最终投标文档

**LLM 必须**:
1. `todo_write` 标记当前步骤为 `blocked`
2. `final_answer(is_blocking=true, answer="")` 等待用户确认
3. answer 必须为空字符串

**等待用户**: 确认最终文档 OK 或请求修改

---

### Step F6: deliver — 交付下载

```
docgen_tender_fill_flow(instance_id="default", action="deliver")
```

**Effect**: 标记流程完成。

**LLM 后续**:
1. 调用 `create_download_url(file_path=<final_word路径>)` 生成下载链接
2. `final_answer` 展示下载链接

---

## artifact 生命周期

```
template (Step3 产出，已确认的投标模板)
  ↓ LLM rag 填充
filled_template (templates/filled_template.json)
  ↓ template_to_markdown
draft md (drafts/final_tender.md)
  ↓ docx generation
final_word (drafts/final_tender.docx)
```

---

## 错误处理

| 场景                      | 处理方式                         |
| ------------------------- | -------------------------------- |
| filled_data_json 解析失败 | 提示 LLM 重新提交正确格式的 JSON |
| docx 生成失败             | 降级为 MD 格式，继续流程         |
| 模板 artifact 不存在      | 提示先完成 Step 3                |
