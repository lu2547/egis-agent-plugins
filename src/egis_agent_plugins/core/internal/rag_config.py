"""RAG 配置 —— egis-agents 真实实现（不再复用 ark-agentic）。

ark-agentic 本身不含 RAG 能力，RAG 相关配置/客户端/服务全部落户 egis-agents。
仅 LLM 的 PA-JT Transport（``PinganEAGWHeaderAsyncTransport``）保留在 ark 侧，
Embedding/Rerank 通过 import 该 Transport 完成网关鉴权。

环境变量（按 ``.env.example`` 的分节顺序）::

    # PostgreSQL (DB_* 首选，WEKNORA_DB_* 兼容降级；DB_* 不加 RAG_ 前缀)
    DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME

    # Milvus（RAG 专属，统一 RAG_ 前缀）
    RAG_MILVUS_HOST / RAG_MILVUS_PORT / RAG_MILVUS_COLLECTION / RAG_MILVUS_METRIC_TYPE

    # Embedding（RAG 专属，统一 RAG_ 前缀）
    RAG_EMBEDDING_PROVIDER=openai|pa_jt
    RAG_EMBEDDING_MODEL / RAG_EMBEDDING_DIMENSION / RAG_EMBEDDING_API_KEY / RAG_EMBEDDING_BASE_URL

    # Rerank（RAG 专属，统一 RAG_ 前缀）
    RAG_RERANK_PROVIDER=openai|pa_jt
    RAG_RERANK_MODEL / RAG_RERANK_API_KEY / RAG_RERANK_BASE_URL / RAG_RERANK_TOP_K / RAG_RERANK_THRESHOLD

    # PA-JT 网关鉴权（provider=pa_jt 时生效，与 LLM 内核共用，不加 RAG_ 前缀）
    PA_JT_OPEN_API_CODE / PA_JT_OPEN_API_CREDENTIAL / PA_JT_RSA_PRIVATE_KEY
    PA_JT_GPT_APP_KEY / PA_JT_GPT_APP_SECRET / PA_JT_SCENE_ID

    # 默认知识库 & 搜索参数（RAG 专属，统一 RAG_ 前缀）
    RAG_DEFAULT_KNOWLEDGE_BASE_IDS (逗号分隔)
    RAG_DEFAULT_TOP_K / RAG_VECTOR_THRESHOLD / RAG_KEYWORD_THRESHOLD
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["RAGConfig", "get_rag_config"]


@dataclass
class RAGConfig:
    """RAG 配置

    Collection 命名规则: ``{milvus_collection}_{embedding_dimension}``
    """

    # ── PostgreSQL ──
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = ""
    db_name: str = "egis"

    # ── Milvus ──
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "knowledge_embeddings"
    milvus_metric_type: Literal["COSINE", "L2", "IP"] = "COSINE"

    # ── 三级知识库 collection 路由（与 WeKnora 端常量保持一致）──
    # personal/public 各一个全局 collection，enterprise 一库一 collection（动态名称）
    personal_collection: str = "personal_knowledge_base"
    public_collection: str = "public_knowledge_base"

    # ── Embedding ──
    embedding_provider: Literal["openai", "pa_jt"] = "openai"
    embedding_model: str = "text-embedding-v4"
    embedding_dimension: int = 1024
    embedding_api_key: str = ""
    embedding_base_url: str = ""

    # ── Rerank ──
    # 是否启用完全由凭证齐全性决定（rerank_base_url / rerank_api_key / rerank_model 缺一不启用）
    rerank_provider: Literal["openai", "pa_jt"] = "openai"
    rerank_model: str = ""
    rerank_api_key: str = ""
    rerank_base_url: str = ""
    rerank_top_k: int = 10
    rerank_threshold: float = 0.3

    # ── PA-JT 网关鉴权（Embedding/Rerank 共用）──
    pa_jt_open_api_code: str = ""
    pa_jt_open_api_credential: str = ""
    pa_jt_rsa_private_key: str = ""
    pa_jt_gpt_app_key: str = ""
    pa_jt_gpt_app_secret: str = ""
    pa_jt_scene_id: str = ""

    # ── 默认知识库 ──
    default_knowledge_base_ids: list[str] = field(default_factory=list)

    # ── 文档摘要 Collection（选文档两段式用，与 WeKnora 三级知识库重构后命名保持一致）──
    summary_collection: str = "summary_knowledge_base"

    # ── 搜索参数 ──
    default_top_k: int = 10
    vector_threshold: float = 0.2
    keyword_threshold: float = 0.3

    # ── Query Rewrite 参数 ──
    rewrite_max_sub_queries: int = 4
    evidence_min_score: float = 0.3
    evidence_rewrite_max_loops: int = 1

    @property
    def db_dsn(self) -> str:
        """PostgreSQL 连接字符串"""
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    def get_collection_name(self, dimension: int | None = None) -> str:
        """获取 Milvus collection 名称

        命名规则::

            {milvus_collection}_{dimension}

        Args:
            dimension: 向量维度；为 None 时取 ``embedding_dimension``
        """
        dim = dimension or self.embedding_dimension
        return f"{self.milvus_collection}_{dim}"


def get_rag_config() -> RAGConfig:
    """从环境变量读取 RAG 配置。"""
    default_kb_ids_str = os.getenv("RAG_DEFAULT_KNOWLEDGE_BASE_IDS", "")
    default_kb_ids = [k.strip() for k in default_kb_ids_str.split(",") if k.strip()]

    return RAGConfig(
        # PostgreSQL（DB_* 首选，WEKNORA_DB_* 兼容）
        db_host=os.getenv("DB_HOST") or os.getenv("WEKNORA_DB_HOST", "localhost"),
        db_port=int(os.getenv("DB_PORT") or os.getenv("WEKNORA_DB_PORT", "5432")),
        db_user=os.getenv("DB_USER") or os.getenv("WEKNORA_DB_USER", "postgres"),
        db_password=os.getenv("DB_PASSWORD") or os.getenv("WEKNORA_DB_PASSWORD", ""),
        db_name=os.getenv("DB_NAME") or os.getenv("WEKNORA_DB_NAME", "egis"),

        # Milvus（RAG 专属，统一 RAG_ 前缀）
        milvus_host=os.getenv("RAG_MILVUS_HOST", "localhost"),
        milvus_port=int(os.getenv("RAG_MILVUS_PORT", "19530")),
        milvus_collection=os.getenv("RAG_MILVUS_COLLECTION", "knowledge_embeddings"),
        milvus_metric_type=os.getenv("RAG_MILVUS_METRIC_TYPE", "COSINE"),  # type: ignore[arg-type]

        # 三级知识库 collection 路由
        personal_collection=os.getenv(
            "RAG_MILVUS_PERSONAL_COLLECTION", "personal_knowledge_base"
        ),
        public_collection=os.getenv(
            "RAG_MILVUS_PUBLIC_COLLECTION", "public_knowledge_base"
        ),

        # Embedding（RAG 专属，统一 RAG_ 前缀；api_key/base_url 缺失时回退 LLM 内核 API_KEY/LLM_BASE_URL）
        embedding_provider=os.getenv("RAG_EMBEDDING_PROVIDER", "openai"),  # type: ignore[arg-type]
        embedding_model=os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-v4"),
        embedding_dimension=int(os.getenv("RAG_EMBEDDING_DIMENSION", "1024")),
        embedding_api_key=os.getenv("RAG_EMBEDDING_API_KEY", os.getenv("API_KEY", "")),
        embedding_base_url=os.getenv("RAG_EMBEDDING_BASE_URL", os.getenv("LLM_BASE_URL", "")),

        # Rerank（是否启用由凭证齐全性决定，无需单独开关；统一 RAG_ 前缀）
        rerank_provider=os.getenv("RAG_RERANK_PROVIDER", "openai"),  # type: ignore[arg-type]
        rerank_model=os.getenv("RAG_RERANK_MODEL", ""),
        rerank_api_key=os.getenv("RAG_RERANK_API_KEY", os.getenv("API_KEY", "")),
        rerank_base_url=os.getenv("RAG_RERANK_BASE_URL", ""),
        rerank_top_k=int(os.getenv("RAG_RERANK_TOP_K", "10")),
        rerank_threshold=float(os.getenv("RAG_RERANK_THRESHOLD", "0.3")),

        # PA-JT 网关鉴权
        pa_jt_open_api_code=os.getenv("PA_JT_OPEN_API_CODE", ""),
        pa_jt_open_api_credential=os.getenv("PA_JT_OPEN_API_CREDENTIAL", ""),
        pa_jt_rsa_private_key=os.getenv("PA_JT_RSA_PRIVATE_KEY", ""),
        pa_jt_gpt_app_key=os.getenv("PA_JT_GPT_APP_KEY", ""),
        pa_jt_gpt_app_secret=os.getenv("PA_JT_GPT_APP_SECRET", ""),
        pa_jt_scene_id=os.getenv("PA_JT_SCENE_ID", ""),

        # 默认知识库
        default_knowledge_base_ids=default_kb_ids,

        # 文档摘要 Collection
        summary_collection=os.getenv("RAG_MILVUS_SUMMARY_COLLECTION", "summary_knowledge_base"),

        # 搜索参数
        default_top_k=int(os.getenv("RAG_DEFAULT_TOP_K", "10")),
        vector_threshold=float(os.getenv("RAG_VECTOR_THRESHOLD", "0.2")),
        keyword_threshold=float(os.getenv("RAG_KEYWORD_THRESHOLD", "0.3")),

        # Query Rewrite 参数
        rewrite_max_sub_queries=int(os.getenv("RAG_REWRITE_MAX_SUB_QUERIES", "4")),
        evidence_min_score=float(os.getenv("RAG_EVIDENCE_MIN_SCORE", "0.3")),
        evidence_rewrite_max_loops=int(os.getenv("RAG_EVIDENCE_REWRITE_MAX_LOOPS", "1")),
    )
