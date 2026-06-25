---
name: PPT Master
description: >
  AI 驱动的 SVG 内容生成系统。基于知识库检索后整理好的 Markdown 内容，
  生成高质量 SVG 页面，并通过多角色协作导出为 PPTX。
  当用户要求"制作PPT"、"生成演示文稿"、"create PPT"、"做PPT"、"ppt-master"时使用此技能。
---

# PPT Master 技能

> AI 驱动的 SVG 内容生成系统。将知识库检索后整理好的 Markdown 内容生成高质量 SVG 页面，通过多角色协作导出 PPTX。

**核心 Pipeline（4 大 Phase）**:

| Phase             | 涵盖步骤                  | 职责                                                                                           |
| ----------------- | ------------------------- | ---------------------------------------------------------------------------------------------- |
| **Phase 1: 准备** | Step 1–3                  | 知识库内容入场 + 项目初始化 + 模板选择（所有复制粘贴、目录创建、模板拷贝等准备工作都在此完成） |
| **Phase 2: 大纲** | Step 4.1–4.5              | Strategist 大纲生成 + 用户确认（输出到 outline_card）                                          |
| **Phase 3: 生成** | Step 4.6–4.8 + Step 5–6.5 | 设计参数 + Executor SVG 逐页生成 + 后处理 + 生成预览                                           |
| **Phase 4: 下载** | Step 7                    | 导出 PPTX + 下载链接卡片                                                                       |

> 当作为 material_maker agent 的子流程被调用时，Phase 1–2 由 material_flow skill 编排，
> PPT Master 主要负责 Phase 3（生成）和 Phase 4（下载）的执行。

> [!CAUTION]
> ## 全局执行纪律（强制）
>
> **本流程为严格串行 Pipeline，以下规则优先级最高——违反任意一条即为执行失败：**
>
> 1. **串行执行** — 步骤必须按序执行；每步输出是下一步输入。非 BLOCKING 的相邻步骤满足前置条件后可连续推进，无需等待用户说"继续"
> 2. **BLOCKING = 强制停止** — 标记 ⛔ BLOCKING 的步骤必须完全停止，AI 必须等待用户明确回复后才能继续，不得代用户做决定
> 3. **禁止跨阶段捆绑** — 禁止跨阶段捆绑执行。Step 4 有两道独立 ⛔ BLOCKING：4.5 内容大纲确认、4.7 设计参数确认（品牌模板 2 项 / 无模板 8 项）。两者必须分两轮完成，严禁在同一个 `final_answer` 中同时输出大纲和设计参数。
> 4. **入场前检查门** — 每步顶部列有前置条件（🚧 GATE），开始前必须验证
> 5. **禁止投机执行** — 禁止为后续步骤"提前准备"内容（如在 Strategist 阶段写 SVG 代码）
> 6. **禁止子代理生成 SVG** — Step 6 SVG 生成依赖上下文，必须由当前主代理端到端完成，禁止委托给子代理
> 7. **仅允许顺序页面生成** — Step 6 中，全局设计上下文确认后，SVG 页面必须按顺序逐页生成，禁止分批
> 8. **禁止“描述即将调用”循环** — 凡需要调用工具时，必须直接发出 tool_call，**严禁**输出“我理解了，让我……”、“我将调用……”、“我要创建文件……”等描述性文字代替实际工具调用；遇到 ⛔ BLOCKING 步骤时，须通过 `final_answer` 将问题呈现给用户并结束本轮，等待用户回复后再继续
> 9. **禁止调用未注册工具** — 严禁调用 `bash`、`create_file`、`write_text_file`、`list_directory`、`read_file` 等未在本 Agent 注册的工具；文件读取必须通过 **`ppt_read_file`** 工具，文件写入必须通过 **`ppt_write_file`** 工具，图标搜索通过 **`ppt_search_icons`** 工具
> 10. **BLOCKING 前必须更新计划** — 进入任何 ⛔ BLOCKING 步骤前，必须先调用 `todo_write` 将当前步骤标记为 `blocked`，然后再调用 `final_answer` 输出确认内容。严禁在未更新计划状态的情况下直接 `final_answer`
> 11. **禁止生成精简版 / 代表性版** — page_plan 所定页数为 **唯一交付基准**，必须逐页完整生成。**严禁**输出"先生成精简版"、"先生成代表性页面"、"几个关键页"、"向您展示完整流程"、"快速版本"、"示例版本"、"部分页面"等类似描述，不得以"工具调用过多"、"费时较长"、"需要大量代码"、"为了高效完成任务"为理由以任何形式减少页数。如果页数过多需分批交付，只能是各批都生成完整页面（如先交 1-9 页、再交 10-18 页），而不是跳页或裁减
> 12. **入场首次 todo_write 必须原地替换占位粗步骤** — 上游 React 计划中常带一条“调用 PPT Master 生成 N 页”类占位粗步骤。进入本 skill 后的首次 `todo_write`，必须用下文的 Step 1→Step N 【整体替换】那条粗步骤，保留之前已 `completed` 的前置步骤，只把当前真正在做的那一步（通常是“整理 source_data.md 草稿”）标 `in_progress`，其余 `pending`。严禁把“调用 PPT Master…”与 Step 1→Step N 并列保留

> [!IMPORTANT]
> ## 语言与沟通规则
>
> - **回复语言**：始终匹配用户输入和源材料的语言
> - **显式覆盖**：若用户明确要求特定语言，则使用该语言
> - **模板格式**：`design_spec.md` 必须始终遵循英文模板结构（章节标题、字段名），内容值可使用用户语言

> [!IMPORTANT]
> ## 资源目录
>
> - **模板/图标/references**：在环境变量 `PPT_MASTER_SKILL_DIR` 指定的目录中（egis-agents 项目内 `pptmaster/` 目录）
> - **templates/layouts/**：可用布局模板（通过读取 `layouts_index.json` 查询）
> - **templates/charts/**：可视化 SVG 模板
> - **templates/icons/**：图标库（chunk/、tabler-filled/、tabler-outline/）
> - **references/**：角色定义文档（strategist.md、executor-base.md 等）

---

## 工作流程

---

## Phase 1: 准备

> 所有准备工作（素材整理、目录创建、模板拷贝等）都在此 Phase 完成。
> 涵盖 Step 1（知识库内容入场）、Step 2（项目初始化）、Step 3（模板选择与风格提取）。

---

### Step 1: 知识库内容入场

🚧 **GATE**: 当前上下文中已经有 `rag` 返回并整理好的文本素材，或用户直接提供了可用于生成 PPT 的 Markdown/文本内容。

> [!IMPORTANT]
> ## 内容入场边界
>
> PPT Master 的输入是已经整理好的 Markdown/文本素材，不负责在主流程中转换 PDF/DOCX/PPTX/URL 原文件。
>
> 在企业知识库问答场景中，知识库文档应先由上游检索技能读取并整理为素材，再进入 PPT Master。若当前上下文还没有可用文本素材，应回到上游资料准备 step 获取内容；严禁在 PPT Master 主流程内临时尝试文档转换。

将已读取的知识库内容整理为 `source_data.md` 草稿，要求：

- 保留关键事实、数字、表格口径、来源文档标题或 chunk 引用信息。
- 删除与 PPT 主题无关的噪音内容。
- 用户要求页数、不要封面、不要目录等限制必须写入草稿顶部的任务约束。
- 暂不写文件；等待 Step 2 创建项目目录后再落盘。

**✅ Checkpoint — 知识库内容已整理成 source_data.md 草稿，进入 Step 2。**

---

### Step 2: 项目初始化

🚧 **GATE**: Step 1 完成；已有可写入项目的 `source_data.md` 草稿。

1. 调用 `ppt_project_init` 工具创建项目目录结构（传入 `project_name` 和 `projects_dir`）
2. 调用 `ppt_write_file`，file_path=`<project_path>/sources/source_data.md`，写入 Step 1 整理好的 Markdown 内容
3. **强制**：若 `PPT_MASTER_DEFAULT_LAYOUT` 已设置（system protocol 中可见），必须调用 `ppt_copy_layout` 工具，传入 `layout_name=PPT_MASTER_DEFAULT_LAYOUT 的值` 和 `project_path`，将模板复制到项目目录。此步骤不可省略。

**✅ Checkpoint — 项目结构已创建，sources/ 目录包含 source_data.md，进入 Step 3。**

---

### Step 3: 模板选择与风格提取

🚧 **GATE**: Step 2 完成；项目目录结构完整，source_data.md 就绪。

⛔ **BLOCKING**: 若 `PPT_MASTER_DEFAULT_LAYOUT` 未设置且用户尚未明确表示是否使用模板，必须呈现选项并**等待用户明确回复**。若用户已声明"不用模板"或指定了特定模板，直接跳过提示。

**默认模板**：若 `PPT_MASTER_DEFAULT_LAYOUT` 已设置：
1. 调用 `ppt_project_validate` 检查项目结构
2. 若模板尚未复制或校验结果显示缺少模板目录：立即调用 `ppt_copy_layout` 补充复制，传入 `layout_name` 和 `project_path`
3. 模板复制完成后进入风格提取

**模板推荐流程**（仅当 `PPT_MASTER_DEFAULT_LAYOUT` 未设置且用户未决定时）：
调用 `ppt_list_layouts` 工具，列出可用模板及风格描述，给出专业推荐后询问用户：

> AI 推荐：根据您的内容主题，我推荐 **[模板/自由设计]**，原因是...
>
> **A) 使用现有模板** — 应用已验证的结构+风格预设
> **B) 自由设计**（大多数情况推荐）— AI 根据内容定制

选 A 后，调用 `ppt_copy_layout` 工具（传入用户选择的 `layout_name` 和 `project_path`）将模板文件复制到项目目录。

> ⚠️ **强制：模板风格提取（有模板时必须执行）**
> 若项目中已存在模板目录（如 `templates/pingan_style/`），**必须**调用 `ppt_read_file` 读取模板目录下的 `design_spec.md`，从中提取颜色方案、字体方案等风格参数。
> 提取的风格参数将在 Step 4 中作为色彩方案和字体方案的**强制基准**——Strategist 不得自行更改模板定义的颜色和字体。
>
> **提取步骤**：
> 1. 调用 `ppt_read_file` 读取 `<project_path>/templates/<layout_name>/design_spec.md`
> 2. 从中提取：primary/secondary/accent 颜色、font_family、字体大小等关键参数
> 3. 记录这些参数，在 Step 4 输出 spec_lock.md 时**必须使用这些参数**而非自行决定
>
> 若模板目录中无 `design_spec.md`，则读取模板的 SVG 文件（如 `01_cover.svg`）提取颜色和字体信息。

**✅ Checkpoint — 模板已确认，风格参数已提取，进入 Step 4。**

---

## Phase 2: 大纲

> Strategist 阶段：生成详细内容大纲 + 等待用户确认。
> 涵盖 Step 4.1–4.5（读取角色定义 → 生成大纲 → BLOCKING 确认）。

---

### Step 4: Strategist 阶段（⛔ 必须触发用户确认，不可跳过）

🚧 **GATE**: Step 3 完成；模板已确认，风格参数已提取。

> ⚠️ **幂等检测（优先执行）**：按以下顺序检查，命中即停：
> 1. **检查 session state**（`pptmaster_state` 命名空间）：
>    - 若 `outline_confirmed=true` 且 `eight_params_confirmed=true`：说明用户已确认全部，**直接跳到 Step 5 GATE 检查**。
>    - 若 `outline_confirmed=true` 但 `eight_params_confirmed` 不为 true：**跳到 4.6**（输出设计参数），跳过 4.1–4.5。
> 2. **检查磁盘文件**：调用 `ppt_read_file` 工具尝试读取 `<project_path>/spec_lock.md`。
>    - 若文件**存在且非空**：说明用户已在此前会话中完成确认，**直接跳到 Step 5 GATE 检查**，无需重新确认。
>    - 若文件**不存在或为空**：必须执行完整的 Step 4 流程，包含下方的 ⛔ BLOCKING 确认环节。
> 
> 注：session state 只在当前会话有效，跨会话重启后需靠磁盘文件（spec_lock.md）。

> ⚠️ **模板风格强制遵守**：若 Step 3 中提取了模板风格参数（颜色、字体），八项确认中的第 5 项（色彩方案）和字体方案**必须使用模板定义的值**，不得自行更改。这是确保 PPT 视觉风格与模板一致的强制约束。

**执行步骤（严格按序）**：

**4.1** 调用 `ppt_read_file` 工具读取 `references/strategist.md`

**4.2** 调用 `ppt_read_file` 工具读取 `templates/design_spec_reference.md`（了解 I–XI 节结构）

**4.3** 若用户提供了图片，调用 `ppt_analyze_images` 工具（传入 images 目录）获取图片信息
> ⚠️ AI 绝不能直接读取/打开图片文件，所有图片信息必须来自此工具输出

**4.4** 基于 source_data.md 的内容，在对话中输出**详细内容大纲**（**仅输出到对话，禁止写入任何文件**）：

> 大纲格式要求（每页必须包含以下全部字段）：
> ```
> 第 N 页 | [页面类型：封面/目录/章节标题/内容/过渡/结尾]
> 标题：XXX
> 副标题/说明：XXX（若有）
> 核心内容：
>   - 要点1（具体文字，非占位符）
>   - 要点2
>   - 要点3
> 视觉元素建议：图表类型 / 图标 / 纯文字 / 图文混排
> 数据/引用来源：XXX（若涉及数据）
> ```
>
> - 大纲页数须合理（通常 10-20 页），并与用户需求吻合
> - 每页要点须来自 source_data.md 的实际内容，不得使用"详见正文"等空洞占位语

**4.5** ⛔ **BLOCKING — 强制等待用户确认大纲（此步骤不可跳过、不可提前执行 4.6）**

> **执行规则**：
> - 在调用 `final_answer` 前，必须先调用：
>   - `ppt_save_state(key="draft_outline", value="<待确认的大纲 markdown>")`
>   - `ppt_save_state(key="awaiting_confirmation", value="outline")`
> - 必须通过 `final_answer` 将完整大纲呈现给用户，末尾明确询问："以上内容大纲是否满意？如需调整页面顺序、增减内容、修改要点或数字，请直接告知；若满意请回复"确认"。"
> - **当前 `final_answer` 只能包含内容大纲和大纲确认问题，严禁同时输出八项设计参数、design_spec、spec_lock 或任何 4.6 之后的内容。**
> - **在收到用户明确确认之前，严禁写入 design_spec.md、spec_lock.md、SVG、备注或任何 4.6 之后的产物；仅当用户要求补充/核验数据时，允许按下方规则更新 source_data.md**
> - 等待用户回复后：
>   - 若用户提出任何修改意见（包括删减数字、调整顺序、改写要点等）→ 立即在对话中更新大纲，**再次执行 4.5**（可反复循环，直到用户满意）
>   - 若用户要求补充、核验或替换事实/数字/口径 → 保持大纲确认未完成，回到 `[rag]` step 检索并深度阅读补充证据；必要时只允许更新 `<project_path>/sources/source_data.md`，然后重新生成并再次执行 **4.5**。**严禁**进入 4.6、写入 design_spec/spec_lock 或生成 SVG。
>   - 若用户明确表示满意（"确认"/"可以"/"ok"/"好的"/"继续"/"开始"等）→ 执行 **4.6**
> - ❌ **严禁**：用户提修改意见后直接写文件或跳过再次确认

**4.5.1** 用户确认大纲后，调用 `ppt_save_state` 持久化断点状态：
```
ppt_save_state(key="outline", value="<已确认的完整大纲 markdown>")
ppt_save_state(key="outline_confirmed", value="true")
ppt_save_state(key="awaiting_confirmation", value="")
```
> 此状态供续接时幂等检测使用，确认为"续接"路径后无需重新走 4.4/4.5。

---

## Phase 3: 生成

> 设计参数确认 + Executor SVG 逐页生成 + 后处理。
> 涵盖 Step 4.6–4.8（设计参数 + 写文件）、Step 5（图片确认）、Step 6–6.5（Executor + 标注审查）。

---

**4.6** 设计参数建议

🚧 **GATE**: 大纲必须已通过用户确认（4.5 完成）。**强制检查**：确认 session state 中 `pptmaster_state.outline_confirmed=true` 或上一轮 4.5 用户已回复“确认”。若大纲未确认，**严禁**输出设计参数——必须先完成 4.5 BLOCKING。

**品牌模板快速通道**（`PPT_MASTER_DEFAULT_LAYOUT` 已设置且模板已复制）：

当品牌模板已加载时，画布/风格/配色/图标/字体/图片六项由模板 `design_spec.md` 自动继承，**仅输出 2 项建议**：
1. 页数（与已确认大纲页数一致）
2. 目标受众与核心信息

**无品牌模板时（完整 8 项）**：

页数须与已确认大纲一致，其余七项基于大纲结构和模板风格给出建议：
1. 画布格式
2. 页数（与已确认大纲页数一致）
3. 目标受众
4. 风格目标
5. 色彩方案（若有模板，必须使用模板定义的颜色，不得自行更改）
6. 图标使用方式
7. 排版方案
8. 图片使用方式

**4.7** ⛔ **BLOCKING — 强制等待用户确认设计参数（此步骤不可跳过、不可提前执行 4.8）**

> **执行规则**：
> - 在调用 `final_answer` 前，必须先调用：
>   - `ppt_save_state(key="eight_params", value="<待确认的设计参数 JSON 或 markdown>")`
>   - `ppt_save_state(key="awaiting_confirmation", value="eight_params")`
> - 必须通过 `final_answer` 将设计参数建议完整呈现给用户，末尾明确询问：“以上设计规格是否满意？如有调整请告知，满意请回复“确认”。”
> - **当前 `final_answer` 只能包含设计参数建议和确认问题，严禁同时写入或展示 design_spec/spec_lock/Executor 输出。**
> - **在收到用户明确确认之前，严禁写入任何文件（design_spec.md / spec_lock.md）**
> - 等待用户回复后：
>   - 若用户提出修改意见 → 在对话中更新建议，**再次执行 4.7**（可反复循环，直到用户满意）
>   - 若用户明确表示满意（“确认”/“可以”/“ok”/“好的”/“继续”/“开始”等）→ 执行 **4.8**
> - ❌ **严禁**：用户提修改意见后直接写文件或跳过再次确认

**4.7.1** 用户确认设计参数后，调用 `ppt_save_state` 持久化断点状态：
```
ppt_save_state(key="eight_params", value="<已确认的八项参数 JSON 或 markdown>")
ppt_save_state(key="eight_params_confirmed", value="true")
ppt_save_state(key="awaiting_confirmation", value="")
```
> 此状态供续接时幂等检测使用，确认为"续接"路径后无需重新走 4.6/4.7。

**4.8** 两道卡口均已通过后，依次写入文件：
- 调用 `ppt_write_file`，file_path=`<project_path>/design_spec.md`，写入完整设计规格（含已确认大纲 + 设计参数）
- 调用 `ppt_write_file`，file_path=`<project_path>/spec_lock.md`，写入机器可读的执行契约内容（参考 `templates/spec_lock_reference.md`）
- 调用 `ppt_save_state`，`key="step"` `value="4"`，标记 Step 4 完成
> `pptmaster_state.step="4"` 配合 `outline_confirmed=true` + `eight_params_confirmed=true`，续接时可直接判断 Executor（Step 5-7）是否已解锁。

**✅ Checkpoint — 内容大纲（4.5）和设计参数（4.7）均已获用户确认，design_spec.md 和 spec_lock.md 已写入，进入 Step 5。**

---

### Step 5: 图片素材确认（跳过或使用用户提供素材）

🚧 **GATE**: Step 4 完成；设计规格和内容提纲已生成（用户已确认）。

> 本系统**不提供 AI 文生图能力**。PPT 中的图片来源仅限：
> - 用户上传的素材（已在 Step 4.3 通过 `ppt_analyze_images` 分析）
> - SVG 矢量图形（Executor 阶段直接绘制）
> - 模板自带的装饰素材
>
> 若用户提供了图片且已分析就绪，直接进入 Step 6。
> 若无图片素材需求，**跳过此步，直接进入 Step 6**。

**✅ Checkpoint — 图片素材已确认（或无需图片），进入 Step 6。**

---

### Step 6: Executor 阶段

🚧 **GATE**: Step 4（以及触发时 Step 5）完成；所有前置交付物就绪。

> ⚠️ **断点续跑检测（优先执行）**：首先检查 session state 中 `pptmaster_state.outline_confirmed` 和 `pptmaster_state.eight_params_confirmed` 是否均为 `true`（确认 Strategist 阶段已完成）。然后调用 `ppt_read_file` 工具读取 `<project_path>/spec_lock.md` 了解全局设计参数。若 `svg_output/` 中已有部分 SVG 文件，从**最后一页编号后**继续生成，跳过已完成的页面（不覆写已有 SVG）。

> 🔒 **页数硬性闸门（强制，优先级最高）**：
> 1. 生成第一个 SVG 前，调用 `ppt_read_file` 读取 `<project_path>/design_spec.md` 中的 page_plan，确认目标总页数 N
> 2. 列出 `svg_output/` 现有文件数 M（续接场景），计算还需生成 N-M 页
> 3. **必须逐页生成全部 N 页 SVG，严禁跳页、省略、以"代表性页面"替代、或以"先生成核心页"为由减少页数**——即使工具调用多、耗时长，也不得裁减
> 4. 所有 SVG 生成完毕后、进入后处理前，再次列出 `svg_output/` 文件数，确认等于 N；若 < N，必须补齐缺失页号，不得进入后处理

根据选定风格调用 `ppt_read_file` 工具读取角色定义：
- `references/executor-base.md` — 必读：通用指南
- `references/executor-general.md` / `references/executor-consultant.md` / `references/executor-consultant-top.md` — 按风格选读一个

**设计参数确认（强制）**：生成第一个 SVG 前，必须回顾并输出关键设计参数（画布尺寸、色彩方案、字体方案、正文字号）。

**每页重读 spec_lock（强制）**：生成**每个** SVG 页面前，必须调用 `ppt_read_file` 工具读取 `<project_path>/spec_lock.md`，只使用其中列出的颜色/字体/图标/图片。

> ⚠️ **主代理规则**：SVG 生成必须由当前主代理完成，禁止委托给子代理。
> ⚠️ **节奏规则**：确认全局设计参数后，Executor 必须顺序逐页生成，保持同一连续上下文。

**视觉构建阶段**：
- 按顺序逐页生成 SVG，每页调用 `ppt_write_file` 写入 `<project_path>/svg_output/<幻灯片编号>.svg`

**逻辑构建阶段**：
- 调用 `ppt_write_file` 将演讲备注写入 `<project_path>/notes/total.md`

**✅ Checkpoint — 确认 `svg_output/` 文件数等于 page_plan 目标页数（无跳页、无缺失），进入 Step 6.5 标注审查。**

---

### Step 6.5: 标注审查与可视化编辑（可选）

🚧 **GATE**: Step 6 完成；所有 SVG 已输出到 `svg_output/`。

> 此步为**可选环节**，仅在 SVG 中使用了编辑标注（`data-edit-target` / `data-edit-annotation` 属性）时触发。

**6.5.1** 调用 `ppt_check_annotations` 工具扫描 `svg_output/` 目录：
- 若无标注点 → **直接跳入 Step 7**
- 若发现标注点 → 通过 `final_answer` 向用户报告待修改元素清单

**6.5.2** ⶸ **BLOCKING — 征询用户是否需要可视化编辑**
- 若用户回复“不用”/“跳过”/“继续” → 进入 Step 7
- 若用户要求编辑 → 调用 `ppt_svg_editor` 启动可视化编辑器，工具返回的 URL 会自动以 iframe 嵌入到 Studio 对话区
- 用户完成编辑后回复“完成”/“继续” → 进入 Step 7

**✅ Checkpoint — 标注审查完成（或跳过），直接进入 Step 7 后处理。**

---

## Phase 4: 下载

> 导出 PPTX + 生成下载链接卡片。
> 涵盖 Step 7（后处理与导出）全部子步骤。

---

### Step 7: 后处理与导出

🚧 **GATE**: Step 6 完成；所有 SVG 已输出到 `svg_output/`；备注 `notes/total.md` 已生成。

> ⚠️ 以下三个子步骤**必须逐一单独执行**，每个步骤完成确认成功后再运行下一个。

**Step 7.1** — 拆分演讲备注：调用 `ppt_split_notes` 工具，传入 `project_path`

**Step 7.2** — SVG 后处理（图标嵌入/图片裁剪嵌入/文本展平/圆角矩形转路径）：调用 `ppt_finalize_svg` 工具，传入 `project_path`

**Step 7.3** — 导出 PPTX（默认嵌入备注）：调用 `ppt_export_pptx` 工具，传入 `project_path` 和 `stage="final"`

**Step 7.4** — 生成下载链接：调用 `create_download_url` 工具，传入 `project_path`，工具将自动定位导出的 PPTX 并返回可点击的下载链接，**必须将链接展示给用户**。

> ❌ **禁止**用文件复制代替 `ppt_finalize_svg`——它执行多个关键处理步骤
> ❌ **禁止**直接从 `svg_output/` 导出——必须使用 `stage="final"` 从 `svg_final/` 导出
> ❌ **禁止**跳过 Step 7.4——PPT 生成完成后必须附上下载链接

> ℹ️ **性能说明**：`ppt_finalize_svg` 和 `ppt_export_pptx` 内部已并发处理多页 SVG（线程池），调用方无需也不应该把 Step 7.2 / 7.3 拆成多个工具调用——单次工具调用即处理整个项目所有 SVG。

---

## 角色切换协议

切换角色前，**必须先读取**对应引用文件——禁止跳过。输出标记：

```markdown
## [角色切换: <角色名>]
📖 读取角色定义: references/<filename>.md
📋 当前任务: <简要描述>
```

---

## 可用工具索引

| 工具名                  | 用途                               | 对应步骤     |
| ----------------------- | ---------------------------------- | ------------ |
| `ppt_project_init`      | 创建项目目录结构                   | Step 2       |
| `ppt_write_file`        | 写入 source_data/design/SVG 等文件 | Step 2/4/5/6 |
| `ppt_import_sources`    | 导入图片等补充素材到项目           | 辅助         |
| `ppt_project_validate`  | 验证项目结构                       | Step 2/任意  |
| `ppt_list_layouts`      | 列出可用布局模板                   | Step 3       |
| `ppt_copy_layout`       | 复制布局模板到项目目录             | Step 3       |
| `ppt_read_file`         | 读取 skill 资源或项目文件          | Step 4/6     |
| `ppt_search_icons`      | 搜索图标库                         | Step 6       |
| `ppt_analyze_images`    | 分析图片尺寸/布局建议              | Step 4       |
| `ppt_check_annotations` | 扫描 SVG 编辑标注                  | Step 6/诊断  |
| `ppt_svg_editor`        | 启动 SVG 可视化编辑器              | 辅助         |
| `ppt_register_template` | 注册/刷新布局模板索引              | Step 3/维护  |
| `ppt_split_notes`       | 拆分演讲备注                       | Step 7.1     |
| `ppt_finalize_svg`      | SVG 后处理                         | Step 7.2     |
| `ppt_export_pptx`       | 导出 PPTX                          | Step 7.3     |
| `create_download_url`   | 生成文件下载链接                   | Step 7.4     |
| `ppt_svg_quality_check` | SVG 质量检查                       | 诊断         |
| `ppt_update_spec`       | 传播 spec_lock 变更                | 维护         |
| `ppt_save_state`        | 持久化 PPT 管道断点状态            | Step 4       |

---

## 参考资源

所有参考文档均在 `PPT_MASTER_SKILL_DIR/references/` 目录下：

| 资源              | 文件                                    |
| ----------------- | --------------------------------------- |
| 共享技术约束      | `references/shared-standards.md`        |
| 画布格式规范      | `references/canvas-formats.md`          |
| 图片布局规范      | `references/image-layout-spec.md`       |
| SVG 图片嵌入      | `references/svg-image-embedding.md`     |
| Strategist 角色   | `references/strategist.md`              |
| Executor 基础     | `references/executor-base.md`           |
| Executor 通用风格 | `references/executor-general.md`        |
| Executor 咨询风格 | `references/executor-consultant.md`     |
| Executor 顶级咨询 | `references/executor-consultant-top.md` |
| 模板设计器        | `references/template-designer.md`       |

---

## 系统状态

- 当前时间: {{current_time}}
- 用户语言: {{language}}
