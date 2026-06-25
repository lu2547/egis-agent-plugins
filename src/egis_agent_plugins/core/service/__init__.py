"""跨域通用服务层

聚合项目中被多个域复用的基础设施客户端：
- ``base/``：基础设施客户端（PG / Milvus）
- ``download/``：下载 token / 路径守卫 / 文件列举

Agent 级 Builder 已统一到 ``core.bootstrap``（skill_builder / agent_builder）。
RAG 服务已下沉到 ``core.flows.rag``（stages/* 阶段化子模块 + clients.py / filters.py / state.py 横切）；
跨层复用的 ``RAGConfig`` 位于 ``core.internal``。
"""
