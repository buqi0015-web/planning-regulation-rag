from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import EMBEDDING, ROOT, ask, connect, ingest_directory, retrieve
from .feedback import save_feedback


app = FastAPI(
    title="城市规划法规 RAG 知识库",
    description="北京市规划规范的条款切块、混合检索、质量治理与引用回答演示。",
    version="0.2.0",
)
STATIC_DIR = ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class SearchRequest(BaseModel):
    query: str = Field(min_length=2)
    top_k: int = Field(default=5, ge=1, le=20)
    jurisdiction: str | None = None


class FeedbackRequest(BaseModel):
    query: str = Field(min_length=2)
    answer: str = Field(min_length=1)
    rating: str = Field(pattern="^(helpful|bad)$")
    reason: str = ""
    citations: list[dict[str, object]] = Field(default_factory=list)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, object]:
    conn = connect()
    documents = conn.execute(
        """
        SELECT COUNT(*) FROM documents
        WHERE json_extract(metadata_json, '$.content_type') IN ('body', 'table', 'figure')
        """
    ).fetchone()[0]
    chunks = conn.execute(
        """
        SELECT COUNT(*) FROM chunks
        WHERE json_extract(metadata_json, '$.content_type') IN ('body', 'table', 'figure')
        """
    ).fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "documents": documents,
        "chunks": chunks,
        "embedding_signature": EMBEDDING.signature,
    }


@app.get("/project-status")
def project_status() -> dict[str, object]:
    conn = connect()
    regulations = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE json_extract(metadata_json, '$.content_type') = 'body'"
    ).fetchone()[0]
    conn.close()
    return {
        "status": "ok",
        "name": "北京市规划法规知识库",
        "regulations": regulations,
    }


@app.get("/documents")
def documents() -> dict[str, object]:
    conn = connect()
    rows = conn.execute(
        "SELECT title, path, metadata_json, updated_at FROM documents ORDER BY title"
    ).fetchall()
    conn.close()
    return {
        "documents": [
            {
                "title": row["title"],
                "path": row["path"],
                "metadata": json.loads(row["metadata_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    }


@app.get("/architecture")
def architecture() -> dict[str, object]:
    return {
        "current_mvp": {
            "document_store": "SQLite",
            "keyword_retrieval": "SQLite FTS5 + 字符词项融合",
            "embedding": EMBEDDING.signature,
            "fusion": "RRF",
            "generation": "OpenAI兼容API或证据摘录式回答",
        },
        "production_recommendation": {
            "document_store": "PostgreSQL / 对象存储",
            "keyword_retrieval": "Elasticsearch BM25",
            "vector_database": "Qdrant",
            "embedding": "text-embedding-v4，按评测决定是否升级",
            "reranker": "Qwen3-Reranker",
            "generation": "模型路由：高频简单问答用小模型，复杂归纳用强模型",
        },
    }


@app.post("/ingest")
def ingest() -> dict[str, object]:
    results = ingest_directory()
    return {"results": results}


@app.post("/search")
def search(request: SearchRequest) -> dict[str, object]:
    filters = {"jurisdiction": request.jurisdiction or "北京市"}
    return {"results": retrieve(request.query, request.top_k, filters)}


@app.post("/ask")
def answer(request: SearchRequest) -> dict[str, object]:
    filters = {"jurisdiction": request.jurisdiction or "北京市"}
    return ask(request.query, request.top_k, filters)


@app.post("/feedback")
def feedback(request: FeedbackRequest) -> dict[str, object]:
    return save_feedback(
        query=request.query,
        answer=request.answer,
        rating=request.rating,
        reason=request.reason,
        citations=request.citations,
    )
