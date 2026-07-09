# RAG 文档读取策略改造方案

## 背景

当前 RAG 的主要问题不是 rerank 模型本身，而是 chunk 切片质量不稳定。全局 chunk rerank 会直接面对大量碎片化候选，当 chunk 边界不准时，rerank 容易把语义上“看起来像”的碎片排到前面，导致最终证据不稳定。

本次改造目标是保留现有 rerank 模式，同时新增一种“按文档处理”的读取模式：文档先选准，再根据文档大小决定读法。小文件直接全文读，大文件才做文件内 chunk recall + rerank + 扩块。

## 总体流程

公共前置流程保持不变：

```text
rewrite/analyze
-> route
-> select document
-> document read strategy
-> context compose
```

`select document` 之后增加一个读取策略开关：

```text
RAG_DOCUMENT_READ_MODE=global_chunk_rerank | per_document_read
```

`global_chunk_rerank` 是当前模式：

```text
selected documents
-> chunk recall
-> global rerank + mmr
-> rag read expansion
-> evidence
```

`per_document_read` 是新增模式：

```text
selected documents
-> for each document:
     small document: full read
     large document: file-scoped chunk recall + rerank top10 + expansion
-> evidence merge + dedup
```

## 文档选择调整

文档选择仍然基于：

```text
summary initial recall
-> filename semantic score
-> summary rerank score
-> weighted final score
```

调整默认参数：

```text
RAG_DOCUMENT_SELECT_RECALL_TOP_K=60
RAG_DOCUMENT_SELECT_FILENAME_WEIGHT=0.8
RAG_DOCUMENT_SELECT_SUMMARY_WEIGHT=0.2
RAG_DOCUMENT_SELECT_FINAL_TOP_K=3
RAG_DOCUMENT_SELECT_RELATIVE_SCORE=0.85
RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY=mmr
RAG_DOCUMENT_SELECT_MMR_LAMBDA=0.7
```

含义：

- 默认从 summary collection 初召回 60 个候选文档，再进入文件名/摘要融合打分。
- 文档融合分数默认按 `filename * 0.8 + summary * 0.2`，因为 summary 内容可能不准，文件名/标题匹配优先。
- 默认最多选择 3 个文档。
- 只有 `doc.final_score >= best_score * 0.85` 的文档才进入后续读取。
- `RAG_DOCUMENT_SELECT_MIN_SCORE` 可以保留，用作绝对下限。
- 默认最终选择使用 MMR 多样性去重，避免多个相似摘要/副本文档挤占最终名额；需要纯分数排序时设为 `RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY=score`。
- `RAG_DOCUMENT_SELECT_MMR_LAMBDA` 控制相关性与多样性的权衡，越接近 1 越偏向分数，越接近 0 越偏向差异化。

这样能减少弱相关文档混入，尤其是“文件名/摘要擦边但不是用户指定资料”的情况。

## 两种读取模式

### 1. global_chunk_rerank

保留当前逻辑，适合原本 chunk 切片质量较好的知识库：

```text
selected docs 内召回 chunk candidates
-> 对所有 candidates 做统一 rerank
-> MMR 去冗余
-> 小文档全文 / 大文档扩块
```

该模式可以作为默认稳定路径，也方便和新增模式做 A/B。

### 2. per_document_read

新增模式，核心是把 rerank 限定在单个文档内部，降低全局碎片噪音。

#### 小文件

判断条件：

```text
chunk_count < RAG_SMALL_DOC_CHUNK_LIMIT
```

默认：

```text
RAG_SMALL_DOC_CHUNK_LIMIT=50
```

处理逻辑：

```text
read all chunks by knowledge_id ordered by chunk_index
-> evidence read_mode=small_doc_full
```

注意：

- 小文件仍然来自 `select document`，不再额外依赖 chunk rerank 决定是否读取。
- 需要保留命中的文档分数、文件名分数、摘要分数，便于 trace 解释为什么读这个文件。

#### 大文件

判断条件：

```text
chunk_count >= RAG_SMALL_DOC_CHUNK_LIMIT
```

处理逻辑：

```text
file-scoped chunk recall
-> rerank top10 anchors
-> 对每个 anchor 做短块扩展
-> 合并窗口
-> evidence read_mode=large_doc_expand
```

默认参数：

```text
RAG_PER_DOC_CHUNK_RERANK_TOP_K=10
RAG_EXPAND_MIN_BYTES=350
RAG_EXPAND_TARGET_BYTES=1000
RAG_EXPAND_MAX_CHUNKS=50
```

扩展规则：

```text
anchor < 350 bytes:
  沿 next_chunk_id 或 chunk_index 向下扩
  直到窗口内容约 1000 bytes
  最多读取 50 个邻近 chunks
```

如果 anchor 本身已经大于等于 350 bytes，则默认不扩，只保留 anchor。

## 扩块去重

扩块仍然要保留当前类似 weknora 的双层去重，不能因为按文档模式新增逻辑就复制一份不一致实现。

### 第一层：processed_ids

用于收集阶段，防止同一个 chunk 被多个 anchor 重复扩展。

规则：

```python
processed_ids = set()

# 先标记全部 rerank 命中的 anchor
for anchor in anchors:
    processed_ids.add(anchor.chunk_id)

# 扩展邻居时检查
if next_chunk_id and next_chunk_id not in processed_ids:
    additional_ids.append(next_chunk_id)
    processed_ids.add(next_chunk_id)
```

这样当 rerank 命中相邻 chunk A 和 B 时：

```text
A.NextChunkID = B
anchors = [A, B]
processed_ids = {A, B}

expand A -> B 已 processed -> skip
expand B -> A 已 processed -> skip
```

相邻 anchor 不会产生重复内容。

### 第二层：added_chunk_ids

用于最终组装阶段，确保 evidence 里同一个 chunk 只出现一次。

规则：

```python
added_chunk_ids = set()

for evidence in collected_evidence:
    if evidence.chunk_id in added_chunk_ids:
        continue
    output.append(evidence)
    added_chunk_ids.add(evidence.chunk_id)
```

### 窗口合并

仅 ID 去重还不够，大文件扩展需要做窗口级合并：

```text
anchor A expands [A, B, C]
anchor C expands [C, D]
=> merge [A, B, C, D]
```

合并规则：

- 同一 `knowledge_id` 内处理。
- 按 `chunk_index` 排序。
- chunk index 连续或重叠则合并为一个窗口。
- 合并后的窗口再按顺序输出 evidence。
- citation 元数据同时保留：
  - `anchor_chunk_ids`
  - `included_chunk_ids`
  - `read_mode`

## 方法抽取要求

不要在新模式里复制当前 reader 的小文档、扩块、去重逻辑。需要把公共能力抽成私有 service/method。

建议新增或重构为：

```text
_services/document_read_strategy.py
_services/chunk_window_expander.py
```

建议方法：

```python
async def read_documents_by_strategy(
    *,
    clients: RAGClients,
    selected_documents: list[dict],
    query_plan: dict,
    mode: str,
) -> tuple[list[dict], dict]:
    ...
```

```python
async def read_small_document(
    *,
    postgres,
    document: dict,
    chunk_limit: int,
) -> list[dict]:
    ...
```

```python
async def recall_and_rerank_document_chunks(
    *,
    clients: RAGClients,
    document: dict,
    queries: list[str],
    top_k: int,
) -> list[dict]:
    ...
```

```python
async def expand_anchor_windows(
    *,
    postgres,
    anchors: list[dict],
    expand_min_bytes: int,
    expand_target_bytes: int,
    expand_max_chunks: int,
) -> list[dict]:
    ...
```

```python
def merge_chunk_windows(windows: list[dict]) -> list[dict]:
    ...
```

```python
def dedup_evidence_chunks(evidence: list[dict]) -> list[dict]:
    ...
```

其中 `expand_anchor_windows()` 内部统一实现：

- anchor 预标记 `processed_ids`
- 邻居扩展检查 `processed_ids`
- 窗口合并
- 最终 `added_chunk_ids` 去重

当前 `read_ranked_context()` 也应该复用这些方法，避免出现两套路由两套扩块规则。

## Trace 与日志

需要把策略选择和关键分数放到 trace/progress/state 中，方便判断错误发生在哪一步。

建议结构：

```json
{
  "document_read_mode": "per_document_read",
  "selected_documents": [
    {
      "knowledge_id": "...",
      "file_name": "...",
      "final_score": 0.91,
      "filename_score": 0.88,
      "summary_score": 0.92,
      "initial_recall_score": 0.73,
      "chunk_count": 43,
      "read_mode": "small_doc_full"
    }
  ],
  "large_document_reads": [
    {
      "knowledge_id": "...",
      "file_name": "...",
      "chunk_count": 128,
      "anchor_count": 10,
      "expanded_window_count": 6,
      "included_chunk_count": 24,
      "dedup_dropped_count": 5
    }
  ]
}
```

日志至少包含：

```text
[RAG_READ_STRATEGY] mode=per_document_read selected_docs=3 small=2 large=1
[ChunkWindowExpander] anchors=10 windows=6 included=24 processed=29 dedup_dropped=5
```

## Context compose

最终 context composer 不区分证据来自哪种读取模式，统一消费 evidence。

但 evidence 需要带上：

```text
read_mode
knowledge_id
knowledge_title
chunk_id
chunk_index
anchor_chunk_ids
included_chunk_ids
score
document_score
content
```

这样模型回答时仍然只看到统一证据包，前端引用也能正确映射。

## 实施步骤

1. 调整 `select document` 默认参数：
   - `RAG_DOCUMENT_SELECT_FINAL_TOP_K=3`
   - `RAG_DOCUMENT_SELECT_RELATIVE_SCORE=0.85`

2. 抽公共 reader 方法：
   - 小文档全文读取
   - 大文档 anchor 扩展
   - processed_ids 去重
   - added_chunk_ids 去重
   - 窗口合并

3. 新增 `per_document_read` 策略。

4. 保留现有 `global_chunk_rerank` 策略，并改为复用公共扩块/去重方法。

5. 增加 trace/progress 输出。

6. 验证：
   - 单 KB 多文件
   - 一个 KB 下多 tag
   - tag 下指定 files
   - 小文档全文读取
   - 大文档短 anchor 扩展
   - 相邻 anchor 不重复
   - context 中 evidence 数量、长度、引用编号正确

## 验收标准

- `select document` 默认最多 3 个文档，弱相关文档明显减少。
- `per_document_read` 下，小文件不依赖 chunk rerank，能完整进入 evidence。
- 大文件只在文件内做 chunk rerank，top10 anchors 扩展到约 1000 bytes。
- 扩展最多 50 chunks。
- 相邻 anchor 扩展不会重复塞入同一 chunk。
- 当前模式和新模式复用同一套扩块/去重方法。
- trace 能看出：
  - 选了哪些文档
  - 每个文档分数
  - 每个文档走小文件还是大文件
  - 大文件 anchor 数、扩展块数、去重数量
