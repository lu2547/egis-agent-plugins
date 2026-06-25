# DocGen 标书投标模板 Workflow（Step 3）

> docgen_tender_template_flow 的完整执行指南。

---

## 前置条件

- `docgen_project_init` 已完成，session state 中有 `docgen_state.project_id` 和 `docgen_state.project_path`
- `docgen_source_parse_mineru` 已完成，项目中有 `tender_markdown` artifact

---

## 步骤详解

### Step T1: start — 创建模板流程实例

```
docgen_tender_template_flow(instance_id="default", action="start")
```

**Effect**: 从 session ctx 读取 project_id 和 project_path，初始化 instance_data。

---

### Step T2: build — 生成投标模板

```
docgen_tender_template_flow(instance_id="default", action="build")
```

**Effect 内部**:
1. 读取 `tender_markdown` artifact（招标材料 MD）
2. 调用 `extract_sections_from_markdown()` 提取章节结构
3. 调用 `build_tender_template()` 生成投标模板 JSON
4. 保存到 `templates/tender_template.json`
5. 注册 artifact: key="template", kind="template"

**输出**: 模板摘要（章节列表 + 数量）

---

### Step T3: generate — 模板转 Word

```
docgen_tender_template_flow(instance_id="default", action="generate")
```

**Effect 内部**:
1. 读取 `template` artifact
2. 调用 `template_to_markdown()` 转为 MD
3. 保存 MD 到 `drafts/tender_draft.md`
4. 尝试 docx 生成，失败降级为 MD
5. 注册 artifact: key="draft_word"

**输出**: Word 文档路径

---

### Step T4: show — 展示 Word 编辑器 ⛔ BLOCKING

```
docgen_tender_template_flow(instance_id="default", action="show")
```

**on_render**: 渲染 docx-editor A2UI 卡片
- iframe 嵌入 docx-editor
- 展示文档标题

**LLM 必须**:
1. `todo_write` 标记当前步骤为 `blocked`
2. `final_answer(is_blocking=true, answer="")` 等待用户确认
3. answer 必须为空字符串

**等待用户**: 确认模板 OK 或请求修改

---

## artifact 生命周期

```
tender_markdown (Step2 产出)
  ↓ extract_sections
template (templates/tender_template.json)
  ↓ template_to_markdown
draft_word_md (drafts/tender_draft.md)
  ↓ docx generation
draft_word (drafts/tender_draft.docx)
```
