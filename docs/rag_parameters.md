# RAG 全阶段参数配置手册

> 本文档列出 `egis-agent-plugins` 中 RAG 一体化检索工具的所有可配环境变量，按流程阶段分节说明。
> 所有参数均通过 `.env` 文件或系统环境变量配置，未设置时使用默认值。

---

## 0. 工具入参（LLM 调用 `rag` 工具时传入）

> **Research Agent 场景**：`rag` 工具仅接收检索业务输入（query + filters）；项目、Subgoal、Unit 等任务身份由工具从 `active_batch` 自动认领，LLM 不需要传递。

| 参数      | 类型   | 必填 | 描述                                                                                                         |
| --------- | ------ | ---- | ------------------------------------------------------------------------------------------------------------ |
| `query`   | string | 是   | Planning 生成的原子检索问题（在 Research 循环中由 work_item 提供）                                           |
| `filters` | object | 是   | 资料范围过滤条件，必须包含 `filters.rag_filter`（`rag_filter` 为知识库/目录/文件层级数组，由 Planning 锁定） |

### filters 结构

| 字段         | 类型     | 必填 | 描述                                                              |
| ------------ | -------- | ---- | ----------------------------------------------------------------- |
| `rag_filter` | object[] | 是   | 知识库/目录/文件层级过滤数组，每项包含 `knowledge_base_id` 等字段 |

### 已移除参数（v2 之前存在，当前版本不再接受）

| 参数     | 说明                                                                |
| -------- | ------------------------------------------------------------------- |
| `source` | 已移除；检索来源固定为 `internal`（知识库），不再支持 `web`         |
| `hints`  | 已移除；文档匹配策略由环境变量 `RAG_DOCUMENT_MATCH_PREFERENCE` 控制 |

---

## 1. 基础设施配置

### 1.1 PostgreSQL

| 环境变量      | 默认值      | 描述                                     |
| ------------- | ----------- | ---------------------------------------- |
| `DB_HOST`     | `localhost` | 数据库主机地址（兼容 `WEKNORA_DB_HOST`） |
| `DB_PORT`     | `5432`      | 数据库端口（兼容 `WEKNORA_DB_PORT`）     |
| `DB_USER`     | `postgres`  | 数据库用户名（兼容 `WEKNORA_DB_USER`）   |
| `DB_PASSWORD` | `""`        | 数据库密码（兼容 `WEKNORA_DB_PASSWORD`） |
| `DB_NAME`     | `egis`      | 数据库名称（兼容 `WEKNORA_DB_NAME`）     |

### 1.2 Milvus 向量数据库

| 环境变量                         | 默认值                    | 描述                                                            |
| -------------------------------- | ------------------------- | --------------------------------------------------------------- |
| `RAG_MILVUS_HOST`                | `localhost`               | Milvus 服务地址                                                 |
| `RAG_MILVUS_PORT`                | `19530`                   | Milvus 服务端口                                                 |
| `RAG_MILVUS_COLLECTION`          | `knowledge_embeddings`    | Chunk 向量 collection 基础名（实际名称为 `{name}_{dimension}`） |
| `RAG_MILVUS_METRIC_TYPE`         | `COSINE`                  | 向量距离度量方式，可选 `COSINE` / `L2` / `IP`                   |
| `RAG_MILVUS_PERSONAL_COLLECTION` | `personal_knowledge_base` | 个人知识库 collection 名                                        |
| `RAG_MILVUS_PUBLIC_COLLECTION`   | `public_knowledge_base`   | 公共知识库 collection 名                                        |
| `RAG_MILVUS_SUMMARY_COLLECTION`  | `summary_knowledge_base`  | 文档摘要 collection 名（用于文档选择阶段）                      |

### 1.3 Embedding 向量化

| 环境变量                  | 默认值              | 描述                             |
| ------------------------- | ------------------- | -------------------------------- |
| `RAG_EMBEDDING_PROVIDER`  | `openai`            | 向量化提供商：`openai` / `pa_jt` |
| `RAG_EMBEDDING_MODEL`     | `text-embedding-v4` | 向量化模型名称                   |
| `RAG_EMBEDDING_DIMENSION` | `1024`              | 向量维度                         |
| `RAG_EMBEDDING_API_KEY`   | 回退 `API_KEY`      | Embedding 服务 API Key           |
| `RAG_EMBEDDING_BASE_URL`  | 回退 `LLM_BASE_URL` | Embedding 服务基础 URL           |

### 1.4 Rerank 重排模型

| 环境变量               | 默认值         | 描述                                |
| ---------------------- | -------------- | ----------------------------------- |
| `RAG_RERANK_PROVIDER`  | `openai`       | 重排提供商：`openai` / `pa_jt`      |
| `RAG_RERANK_MODEL`     | `""`           | 重排模型名称（留空则不启用 rerank） |
| `RAG_RERANK_API_KEY`   | 回退 `API_KEY` | Rerank 服务 API Key                 |
| `RAG_RERANK_BASE_URL`  | `""`           | Rerank 服务基础 URL                 |
| `RAG_RERANK_TOP_K`     | `10`           | Rerank 配置级 top_k（基础配置层）   |
| `RAG_RERANK_THRESHOLD` | `0.7`          | Rerank 分数阈值（低于此值过滤）     |

### 1.5 PA-JT 网关鉴权（provider=pa_jt 时生效）

| 环境变量                    | 默认值 | 描述               |
| --------------------------- | ------ | ------------------ |
| `PA_JT_OPEN_API_CODE`       | `""`   | 开放平台 API Code  |
| `PA_JT_OPEN_API_CREDENTIAL` | `""`   | 开放平台凭证       |
| `PA_JT_RSA_PRIVATE_KEY`     | `""`   | RSA 私钥（签名用） |
| `PA_JT_GPT_APP_KEY`         | `""`   | GPT 应用 Key       |
| `PA_JT_GPT_APP_SECRET`      | `""`   | GPT 应用 Secret    |
| `PA_JT_SCENE_ID`            | `""`   | 场景 ID            |

---

## 2. 查询改写阶段（Rewrite）

| 环境变量                         | 默认值 | 描述                 |
| -------------------------------- | ------ | -------------------- |
| `RAG_REWRITE_MAX_SUB_QUERIES`    | `4`    | 最大子查询拆分数量   |
| `RAG_EVIDENCE_MIN_SCORE`         | `0.3`  | 证据最低分数阈值     |
| `RAG_EVIDENCE_REWRITE_MAX_LOOPS` | `1`    | 证据改写最大循环次数 |

---

## 3. 文档选择阶段（Document Selection）

### 3.1 召回参数

| 环境变量                           | 默认值                         | 描述                                          |
| ---------------------------------- | ------------------------------ | --------------------------------------------- |
| `RAG_DOCUMENT_SELECT_TOP_K`        | `20`                           | 文档选择初始召回数量（每个 scope×query 维度） |
| `RAG_DOCUMENT_SELECT_RECALL_TOP_K` | `60`                           | 文档回忆阶段的扩展 top_k                      |
| `RAG_DOCUMENT_SELECT_RRF_K`        | `60`                           | RRF（Reciprocal Rank Fusion）融合排序参数 k   |
| `RAG_SCOPE_SELECT_CONCURRENCY`     | 同 `RAG_RETRIEVAL_CONCURRENCY` | 文档选择时的并发检索数                        |

### 3.2 评分与匹配策略

| 环境变量                                | 默认值             | 描述                                                                                                          |
| --------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------- |
| `RAG_DOCUMENT_MATCH_PREFERENCE`         | `filename`         | 文档匹配偏好策略：`filename`（文件名优先 0.8:0.2）/ `balanced`（均衡 0.5:0.5）/ `summary`（摘要优先 0.2:0.8） |
| `RAG_DOCUMENT_SELECT_FILENAME_WEIGHT`   | 由 preference 决定 | 文件名评分权重（覆盖 preference 计算值）                                                                      |
| `RAG_DOCUMENT_SELECT_SUMMARY_WEIGHT`    | 由 preference 决定 | 摘要评分权重（覆盖 preference 计算值）                                                                        |
| `RAG_DOCUMENT_FILENAME_SCORE_TIMEOUT_S` | `30`               | 文件名 LLM 评分的超时时间（秒）                                                                               |
| `RAG_DOCUMENT_SELECT_RERANK_TIMEOUT_S`  | `30`               | 文档选择阶段 rerank 操作超时时间（秒）                                                                        |

### 3.3 筛选与去冗余

| 环境变量                                 | 默认值 | 描述                                                                        |
| ---------------------------------------- | ------ | --------------------------------------------------------------------------- |
| `RAG_DOCUMENT_SELECT_FINAL_TOP_K`        | `3`    | 最终选出的文档数量上限                                                      |
| `RAG_DOCUMENT_SELECT_MIN_SCORE`          | `0.15` | 文档选择的绝对最低分数阈值                                                  |
| `RAG_DOCUMENT_SELECT_RELATIVE_SCORE`     | `0.85` | 相对最佳分数的 cutoff 比例（实际 cutoff = max(min_score, best × relative)） |
| `RAG_DOCUMENT_SELECT_DIVERSITY_STRATEGY` | `mmr`  | 多样性策略：`mmr`（MMR 去冗余）/ `score`（纯分数排序）                      |
| `RAG_DOCUMENT_SELECT_MMR_LAMBDA`         | `0.7`  | 文档选择阶段 MMR 的 lambda 参数（1=纯相关性, 0=纯多样性）                   |

---

## 4. Chunk 召回阶段（Chunk Recall）

| 环境变量                       | 默认值 | 描述                                    |
| ------------------------------ | ------ | --------------------------------------- |
| `RAG_CHUNK_RECALL_TOP_K`       | `40`   | 全局 chunk 级混合检索的 top_k           |
| `RAG_CHUNK_RECALL_MAX_QUERIES` | `12`   | 单轮最大查询数（补搜轮从 gap 查询中取） |
| `RAG_RETRIEVAL_CONCURRENCY`    | `2`    | 通用检索并发度（多个阶段共享此默认值）  |
| `RAG_DEFAULT_TOP_K`            | `10`   | 默认搜索 top_k（基础配置层）            |
| `RAG_VECTOR_THRESHOLD`         | `0.2`  | 向量检索分数阈值                        |
| `RAG_KEYWORD_THRESHOLD`        | `0.3`  | 关键词检索分数阈值                      |

### 4.1 逐文档模式专属参数

当 `RAG_DOCUMENT_READ_MODE=per_document_read` 时生效：

| 环境变量                         | 默认值                         | 描述                                   |
| -------------------------------- | ------------------------------ | -------------------------------------- |
| `RAG_PER_DOC_RECALL_CONCURRENCY` | 同 `RAG_RETRIEVAL_CONCURRENCY` | 逐文档模式下每文档召回的并发度         |
| `RAG_PER_DOC_CHUNK_RECALL_TOP_K` | `40`                           | 逐文档模式下每个文档的 chunk 召回数量  |
| `RAG_PER_DOC_CHUNK_RERANK_TOP_K` | `10`                           | 逐文档模式下每个文档的 rerank 保留数量 |

---

## 5. 排名阶段（Ranking & Reranking）

| 环境变量                       | 默认值                         | 描述                                                            |
| ------------------------------ | ------------------------------ | --------------------------------------------------------------- |
| `RAG_RANK_TOP_K`               | `10`                           | 排名后最终保留的候选 chunk 数量                                 |
| `RAG_RERANK_TOPN`              | `30`                           | 送入外部 rerank 模型的候选 chunk 数量上限                       |
| `RAG_RERANK_TIMEOUT_S`         | `5`                            | Rerank 模型单次调用超时时间（秒）                               |
| `RAG_RERANK_QUERY_CONCURRENCY` | 同 `RAG_RETRIEVAL_CONCURRENCY` | Rerank 多查询并发度                                             |
| `RAG_MMR_LAMBDA`               | `0.7`                          | Chunk 级 MMR 多样性排序的 lambda 参数（1=纯相关性, 0=纯多样性） |

---

## 6. 深度阅读阶段（Deep Read / Context Expansion）

| 环境变量                    | 默认值                | 描述                                                                                              |
| --------------------------- | --------------------- | ------------------------------------------------------------------------------------------------- |
| `RAG_DOCUMENT_READ_MODE`    | `global_chunk_rerank` | 文档阅读模式：`global_chunk_rerank`（全局 chunk 级重排后读）/ `per_document_read`（逐文档独立读） |
| `RAG_SMALL_DOC_CHUNK_LIMIT` | `50`                  | 小文档判定阈值（chunk 数 < 此值的文档全文通读）                                                   |
| `RAG_EXPAND_MIN_BYTES`      | `350`                 | 短 anchor 最小字节数阈值（低于此值触发向邻近 chunk 扩展）                                         |
| `RAG_EXPAND_TARGET_BYTES`   | `1000`                | 深读扩展的目标字节数（扩展到至少此字节量停止）                                                    |
| `RAG_EXPAND_MAX_CHUNKS`     | `50`                  | 深读扩展最多可读取的邻居 chunk 数量                                                               |
| `RAG_EVIDENCE_TOP_K`        | 同 `RAG_RANK_TOP_K`   | 全局 chunk 模式下读入的证据 top_k                                                                 |

---

## 7. 证据池管理

| 环境变量                          | 默认值 | 描述                                    |
| --------------------------------- | ------ | --------------------------------------- |
| `RAG_EVIDENCE_POOL_MAX_CHUNKS`    | `100`  | 证据池最大 chunk 容量（多轮累积后裁剪） |
| `RAG_EVIDENCE_MAX_CHUNKS`         | `30`   | 最终答案生成使用的证据 chunk 数量上限   |
| `RAG_QUALITY_MAX_EVIDENCE_CHUNKS` | `15`   | 用于质量评估的证据 chunk 数量上限       |

---

## 8. 质量评估阶段（Quality Evaluation）

| 环境变量                      | 默认值 | 描述                                                |
| ----------------------------- | ------ | --------------------------------------------------- |
| `RAG_QUALITY_TIMEOUT_SECONDS` | `60`   | 质量评估 LLM 调用超时时间（秒）                     |
| `RAG_QUALITY_MAX_ROUNDS`      | `5`    | 最大质量评估轮数（含首轮；实际重试次数 = 此值 - 1） |

---

## 9. 默认知识库

| 环境变量                         | 默认值 | 描述                                                  |
| -------------------------------- | ------ | ----------------------------------------------------- |
| `RAG_DEFAULT_KNOWLEDGE_BASE_IDS` | `""`   | 默认知识库 ID 列表（逗号分隔，未指定 filters 时使用） |

---

## 附录：参数生效流程图

```
用户问题
  │
  ▼
┌─────────────────────────────┐
│ 1. Rewrite（查询改写）       │ ← RAG_REWRITE_MAX_SUB_QUERIES
│    意图识别 + 子问题拆分      │   RAG_EVIDENCE_MIN_SCORE
│    生成 doc_query/sub_queries │   RAG_EVIDENCE_REWRITE_MAX_LOOPS
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 2. Document Select（文档选择）│ ← RAG_DOCUMENT_SELECT_TOP_K
│    摘要+元数据混合检索        │   RAG_DOCUMENT_SELECT_FINAL_TOP_K
│    文件名LLM评分 + Rerank    │   RAG_DOCUMENT_MATCH_PREFERENCE
│    RRF融合 → shortlist       │   RAG_DOCUMENT_SELECT_MMR_LAMBDA
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 3. Chunk Recall（块召回）    │ ← RAG_CHUNK_RECALL_TOP_K
│    在选中文档内向量+关键词检索 │   RAG_CHUNK_RECALL_MAX_QUERIES
│    多查询并行召回             │   RAG_RETRIEVAL_CONCURRENCY
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 4. Rank（排名重排）          │ ← RAG_RANK_TOP_K
│    外部Rerank模型打分        │   RAG_RERANK_TOPN / RAG_RERANK_TIMEOUT_S
│    MMR去冗余多样性排序        │   RAG_MMR_LAMBDA
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 5. Deep Read（深度阅读）     │ ← RAG_DOCUMENT_READ_MODE
│    小文档全文通读             │   RAG_SMALL_DOC_CHUNK_LIMIT
│    大文档按anchor动态扩展     │   RAG_EXPAND_MIN_BYTES / TARGET / MAX
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│ 6. Quality Evaluate（质量评估）│ ← RAG_QUALITY_TIMEOUT_SECONDS
│    证据充分性 LLM 评估       │   RAG_QUALITY_MAX_ROUNDS
│    输出 gap_ledger + 补查策略 │   RAG_QUALITY_MAX_EVIDENCE_CHUNKS
└──────────────┬──────────────┘
               │ 未通过
               ▼
┌─────────────────────────────┐
│ 7. Retry（补搜重试）         │ ← max_retries = RAG_QUALITY_MAX_ROUNDS - 1
│    基于 gap 查询补充召回      │   RAG_EVIDENCE_POOL_MAX_CHUNKS
│    累积证据池，再次评估       │   RAG_EVIDENCE_MAX_CHUNKS
└─────────────────────────────┘
```
