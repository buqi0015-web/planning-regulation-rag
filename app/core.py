from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any

from .config import ROOT
from .providers import get_embedding_provider, lexical_terms
from .query_understanding import QueryUnderstanding, understand_query
from .reranker import get_reranker


DB_PATH = ROOT / os.getenv("RAG_DB_PATH", "data/rag.db")
SOURCE_DIR = ROOT / os.getenv("RAG_SOURCE_DIR", "data/source")
ARTICLE_RE = re.compile(r"(?m)^(第[一二三四五六七八九十百千万零〇\d]+条)\s*(.*)$")
STANDARD_CLAUSE_RE = re.compile(r"(?m)^(\d+(?:\.\d+){1,3})\s+(.+)$")
HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+)$")
EMBEDDING = get_embedding_provider()
RERANKER = get_reranker()
TABLE_INTENT_TERMS = (
    "表",
    "清单",
    "对应",
    "优先",
    "次序",
    "阶段",
    "分别",
    "哪种",
    "哪些",
    "什么图纸",
    "照度值",
    "间距系数",
    "长高比",
    "标准号",
    "实施日期",
)
FIGURE_INTENT_TERMS = (
    "图示",
    "示意图",
    "技术图",
    "图中",
    "图纸",
    "附图",
    "基准面",
    "计算起点",
    "扫掠角",
    "建模",
    "复杂形体",
)
CLAUSE_REFERENCE_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)")


def _limit_sections(
    sections: list[tuple[str, str]], max_chars: int, overlap: int = 200
) -> list[tuple[str, str]]:
    limited = []
    step = max(1, max_chars - overlap)
    for section, content in sections:
        if len(content) <= max_chars:
            limited.append((section, content))
            continue
        parts = [content[index:index + max_chars].strip() for index in range(0, len(content), step)]
        limited.extend(
            (f"{section}（续{index}）", part)
            for index, part in enumerate(parts, start=1)
            if part
        )
    return limited


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            title TEXT NOT NULL,
            section TEXT NOT NULL,
            content TEXT NOT NULL,
            search_text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            FOREIGN KEY(document_id) REFERENCES documents(id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            search_text
        );
        """
    )
    return conn


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, text[end + 5 :].strip()


def search_text(text: str) -> str:
    return " ".join(lexical_terms(text))


def cosine(
    left: dict[str, float] | list[float], right: dict[str, float] | list[float]
) -> float:
    if isinstance(left, list) and isinstance(right, list):
        return sum(a * b for a, b in zip(left, right))
    if isinstance(left, list) or isinstance(right, list):
        return 0.0
    assert isinstance(left, dict) and isinstance(right, dict)
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def split_regulation(body: str, max_chars: int = 1200) -> list[tuple[str, str]]:
    matches = list(ARTICLE_RE.finditer(body))
    if matches:
        sections: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            sections.append((match.group(1), body[match.start() : end].strip()))
        return _limit_sections(sections, max_chars)

    clause_matches = list(STANDARD_CLAUSE_RE.finditer(body))
    if clause_matches:
        sections = []
        preamble = body[: clause_matches[0].start()].strip()
        if preamble:
            sections.append(("前置材料", preamble))
        for index, match in enumerate(clause_matches):
            end = clause_matches[index + 1].start() if index + 1 < len(clause_matches) else len(body)
            sections.append((match.group(1), body[match.start() : end].strip()))
        return _limit_sections(sections, max_chars)

    headings = list(HEADING_RE.finditer(body))
    if headings:
        sections = []
        for index, match in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
            sections.append((match.group(1).strip(), body[match.start() : end].strip()))
        return _limit_sections(sections, max_chars)

    return [
        (f"片段 {index // max_chars + 1}", body[index : index + max_chars].strip())
        for index in range(0, len(body), max_chars)
        if body[index : index + max_chars].strip()
    ]


def ingest_file(path: Path, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    conn = conn or connect()
    text = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    title = metadata.get("title") or path.stem
    document_id = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    index_signature = f"{text}\nembedding={EMBEDDING.signature}"
    content_hash = hashlib.sha256(index_signature.encode("utf-8")).hexdigest()

    existing = conn.execute(
        "SELECT content_hash FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if existing and existing["content_hash"] == content_hash:
        if owns_connection:
            conn.close()
        return {"path": str(path), "status": "unchanged", "chunks": 0}

    conn.execute("DELETE FROM chunks_fts WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ?)", (document_id,))
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.execute(
        """
        INSERT INTO documents(id, path, title, content_hash, metadata_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            path=excluded.path, title=excluded.title, content_hash=excluded.content_hash,
            metadata_json=excluded.metadata_json, updated_at=CURRENT_TIMESTAMP
        """,
        (document_id, str(path), title, content_hash, json.dumps(metadata, ensure_ascii=False)),
    )

    sections = split_regulation(body)
    enriched_sections = [f"{title} {section} {content}" for section, content in sections]
    vectors = EMBEDDING.embed_many(enriched_sections)
    for index, ((section, content), vector) in enumerate(zip(sections, vectors)):
        chunk_id = f"{document_id}-{index:04d}"
        enriched = f"{title} {section} {content}"
        conn.execute(
            """
            INSERT INTO chunks(id, document_id, title, section, content, search_text, metadata_json, vector_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk_id,
                document_id,
                title,
                section,
                content,
                search_text(enriched),
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(vector),
            ),
        )
        conn.execute(
            "INSERT INTO chunks_fts(chunk_id, search_text) VALUES (?, ?)",
            (chunk_id, search_text(enriched)),
        )
    conn.commit()
    if owns_connection:
        conn.close()
    return {"path": str(path), "status": "indexed", "chunks": len(sections)}


def ingest_directory(directory: Path = SOURCE_DIR) -> list[dict[str, Any]]:
    conn = connect()
    try:
        results = [
            ingest_file(path, conn)
            for path in sorted(directory.rglob("*"))
            if path.suffix.lower() in {".md", ".txt"}
        ]
        return results
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def prune_index(directory: Path) -> dict[str, int]:
    allowed_paths = {
        str(path.resolve())
        for path in directory.rglob("*")
        if path.suffix.lower() in {".md", ".txt"}
    }
    conn = connect()
    removed_documents = 0
    removed_chunks = 0
    try:
        for row in conn.execute("SELECT id, path FROM documents").fetchall():
            if str(Path(row["path"]).resolve()) in allowed_paths:
                continue
            chunk_ids = [
                item["id"]
                for item in conn.execute(
                    "SELECT id FROM chunks WHERE document_id = ?", (row["id"],)
                ).fetchall()
            ]
            for chunk_id in chunk_ids:
                conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (row["id"],))
            conn.execute("DELETE FROM documents WHERE id = ?", (row["id"],))
            removed_documents += 1
            removed_chunks += len(chunk_ids)
        conn.commit()
    finally:
        conn.close()
    return {"removed_documents": removed_documents, "removed_chunks": removed_chunks}


def _metadata_allowed(metadata: dict[str, Any], filters: dict[str, str] | None) -> bool:
    if metadata.get("status", "有效") not in {"有效", "现行有效", "published"}:
        return False
    return not filters or all(not value or metadata.get(key) == value for key, value in filters.items())


def query_wants_table(query: str) -> bool:
    return understand_query(query).content_intent == "table" or any(term in query for term in TABLE_INTENT_TERMS)


def query_wants_figure(query: str) -> bool:
    return understand_query(query).content_intent == "figure" or any(term in query for term in FIGURE_INTENT_TERMS)


def retrieve(query: str, top_k: int = 5, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
    parsed = understand_query(query)
    retrieval_query = parsed.expanded_query
    candidate_limit = max(int(os.getenv("RERANKER_TOP_N", "50")), top_k * 4, 20)
    conn = connect()
    rows = conn.execute("SELECT * FROM chunks").fetchall()
    vector = EMBEDDING.embed(retrieval_query, is_query=True)
    query_terms = set(lexical_terms(retrieval_query))
    vector_ranked = sorted(
        rows,
        key=lambda row: cosine(vector, json.loads(row["vector_json"])),
        reverse=True,
    )[:candidate_limit]
    overlap_ranked = sorted(
        rows,
        key=lambda row: len(query_terms.intersection(row["search_text"].split()))
        / (len(query_terms) or 1),
        reverse=True,
    )[:candidate_limit]

    fts_rows: list[sqlite3.Row] = []
    terms = lexical_terms(retrieval_query)
    if terms:
        expression = " OR ".join(f'"{term}"' for term in dict.fromkeys(terms[:30]))
        try:
            fts_rows = conn.execute(
                """
                SELECT chunks.* FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.chunk_id
                WHERE chunks_fts MATCH ?
                ORDER BY bm25(chunks_fts)
                LIMIT ?
                """,
                (expression, candidate_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            fts_rows = []

    scores: dict[str, float] = {}
    row_by_id: dict[str, sqlite3.Row] = {}
    for ranked_rows, weight in ((overlap_ranked, 1.2), (fts_rows, 1.0), (vector_ranked, 0.8)):
        for rank, row in enumerate(ranked_rows, start=1):
            scores[row["id"]] = scores.get(row["id"], 0.0) + weight / (60 + rank)
            row_by_id[row["id"]] = row

    clause_references = set(CLAUSE_REFERENCE_RE.findall(retrieval_query))
    results: list[dict[str, Any]] = []
    for chunk_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        row = row_by_id[chunk_id]
        metadata = json.loads(row["metadata_json"])
        if not _metadata_allowed(metadata, filters):
            continue
        raw_authority_weight = float(metadata.get("authority_weight", "1.0"))
        authority_weight = 1 + (raw_authority_weight - 1) * 0.25
        content_type = metadata.get("content_type")
        if content_type == "table":
            content_type_weight = 1.75 if parsed.content_intent == "table" else 0.8
        elif content_type == "figure":
            content_type_weight = 1.75 if parsed.content_intent == "figure" else 0.4
        else:
            content_type_weight = 1.0
        title_weight = 1.35 if row["title"] in retrieval_query else 1.0
        section_weight = (
            3.0
            if any(row["section"] == clause or row["section"].startswith(f"{clause}（") for clause in clause_references)
            else 1.0
        )
        results.append(
            {
                "chunk_id": chunk_id,
                "title": row["title"],
                "section": row["section"],
                "content": row["content"],
                "metadata": metadata,
                "query_understanding": parsed.to_dict(),
                "score": round(
                    score * authority_weight * content_type_weight * title_weight * section_weight,
                    6,
                ),
            }
        )
    conn.close()
    ranked = sorted(results, key=lambda item: item["score"], reverse=True)[:candidate_limit]
    if RERANKER.name != "none":
        ranked = RERANKER.rerank(query, ranked)
    return ranked[:top_k]


def detect_conflicts(results: list[dict[str, Any]]) -> list[str]:
    by_title: dict[str, set[str]] = {}
    for item in results:
        title = item["title"]
        version = item["metadata"].get("version")
        if version:
            by_title.setdefault(title, set()).add(version)
    return [f"检索到《{title}》多个版本：{', '.join(sorted(versions))}" for title, versions in by_title.items() if len(versions) > 1]


def _local_answer(query: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return "知识库中没有找到足够相关的现行有效条文。请缩小地区或补充法规名称，并以主管部门正式文件为准。"
    lines = ["根据知识库中检索到的现行有效材料："]
    item = results[0]
    compact = re.sub(r"\s+", " ", item["content"]).strip()
    lines.append(f"{compact[:500]}{'…' if len(compact) > 500 else ''} [1]")
    lines.append("以上为知识库辅助检索结果，不替代规划主管部门的正式解释或审批意见。")
    return "\n".join(lines)


def assess_evidence_sufficiency(
    query: str,
    results: list[dict[str, Any]],
    parsed: QueryUnderstanding | None = None,
) -> dict[str, Any]:
    parsed = parsed or understand_query(query)
    if not results:
        return {"sufficient": False, "reason": "知识库未检索到足够相关的现行有效依据。"}
    top_score = float(results[0].get("score", 0.0))
    if top_score < float(os.getenv("MIN_EVIDENCE_SCORE", "0.005")):
        return {"sufficient": False, "reason": "检索结果相关性较弱，暂不生成结论。"}
    if parsed.high_risk_intent:
        return {
            "sufficient": False,
            "reason": "该问题涉及审批或最终合规判断，知识库只能提供可核验依据，不能替代主管部门结论。",
        }
    if "密云区" in parsed.jurisdictions and not any(
        item["metadata"].get("jurisdiction") == "密云区" for item in results
    ):
        return {"sufficient": False, "reason": "当前结果未命中密云区资料，不能推断具体控规或地块结论。"}
    if len(results) >= 3:
        top_titles = {item["title"] for item in results[:3]}
        if len(top_titles) == 3 and top_score < 0.02:
            return {"sufficient": False, "reason": "候选依据分散，尚不足以形成稳定回答。"}
    return {"sufficient": True, "reason": ""}


def _insufficient_answer(reason: str) -> str:
    return (
        f"证据不足：{reason}\n"
        "建议补充法规名称、条款号、适用地区或项目场景后重新查询。"
        "以上为知识库辅助检索结果，不替代规划主管部门的正式解释或审批意见。"
    )


def _llm_answer(query: str, results: list[dict[str, Any]]) -> str | None:
    base_url = os.getenv("LLM_BASE_URL", "").rstrip("/")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "")
    if not base_url or not api_key or not model or not results:
        return None
    evidence = "\n\n".join(
        f"[{index}] 《{item['title']}》{item['section']}\n{item['content']}"
        for index, item in enumerate(results, start=1)
    )
    prompt = (
        "你是城市规划法规知识库助手。只能根据证据回答；每个结论必须标注引用编号；"
        "证据不足或冲突时必须明确说明；不得把建议当成正式审批结论；"
        "回答应直接、简洁，优先控制在400个中文字符以内，只保留关键结论、适用条件和引用。\n\n"
        f"问题：{query}\n\n证据：\n{evidence}"
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 600,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def ask(query: str, top_k: int = 5, filters: dict[str, str] | None = None) -> dict[str, Any]:
    parsed = understand_query(query)
    results = retrieve(query, top_k=top_k, filters=filters)
    conflicts = detect_conflicts(results)
    evidence_check = assess_evidence_sufficiency(query, results, parsed)
    if evidence_check["sufficient"]:
        answer = _llm_answer(query, results) or _local_answer(query, results)
    else:
        answer = _insufficient_answer(str(evidence_check["reason"]))
    citations = [
        {
            "id": index,
            "title": item["title"],
            "section": item["section"],
            "source_url": item["metadata"].get("source_url", ""),
            "effective_date": item["metadata"].get("effective_date", ""),
            "jurisdiction": item["metadata"].get("jurisdiction", ""),
            "content_type": item["metadata"].get("content_type", "body"),
            "page": item["metadata"].get("page", ""),
            "table_id": item["metadata"].get("table_id", ""),
        }
        for index, item in enumerate(results, start=1)
    ]
    return {
        "question": query,
        "answer": answer,
        "citations": citations,
        "conflicts": conflicts,
        "results": results,
        "query_understanding": parsed.to_dict(),
        "evidence_check": evidence_check,
        "reranker": RERANKER.name,
    }


def evaluate(dataset_path: Path, top_k: int = 5) -> dict[str, Any]:
    cases = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    details = []
    cutoffs = sorted({1, 3, 5, top_k})
    hit_counts = {cutoff: 0 for cutoff in cutoffs}
    precision_sums = {cutoff: 0.0 for cutoff in cutoffs}
    ndcg_sums = {cutoff: 0.0 for cutoff in cutoffs}
    title_hit_counts = {cutoff: 0 for cutoff in cutoffs}
    reciprocal_rank = 0.0
    category_rows: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        results = retrieve(case["query"], top_k=max(cutoffs), filters=case.get("filters"))
        relevant = case.get("relevant")
        if not relevant:
            relevant = [
                {
                    "title": case.get("expected_title"),
                    "section": case["expected_section"],
                    "content_type": case.get("expected_content_type"),
                }
            ]

        def matches(item: dict[str, Any], target: dict[str, Any]) -> bool:
            title_ok = not target.get("title") or target["title"] == item["title"]
            section_ok = not target.get("section") or target["section"] in item["section"]
            type_ok = (
                not target.get("content_type")
                or target["content_type"] == item["metadata"].get("content_type", "body")
            )
            asset_id = (
                item["metadata"].get("table_id")
                or item["metadata"].get("figure_id")
                or item["chunk_id"]
            )
            asset_ok = not target.get("asset_id") or target["asset_id"] == asset_id
            return title_ok and section_ok and type_ok and asset_ok

        relevance = [int(any(matches(item, target) for target in relevant)) for item in results]
        rank = next((index for index, value in enumerate(relevance, start=1) if value), None)
        matched_targets = [
            target
            for target in relevant
            if any(matches(item, target) for item in results[: max(cutoffs)])
        ]
        required_recall = len(matched_targets) / len(relevant) if relevant else 0.0
        reciprocal_rank += 1 / rank if rank else 0
        expected_titles = {target.get("title") for target in relevant if target.get("title")}
        row = {
            "query": case["query"],
            "category": case.get("category", "uncategorized"),
            "expected": relevant,
            "rank": rank,
            "required_recall": round(required_recall, 4),
            "top1_title": results[0]["title"] if results else "",
            "top1_section": results[0]["section"] if results else "",
            "top1_content_type": results[0]["metadata"].get("content_type", "body") if results else "",
        }
        for cutoff in cutoffs:
            selected = relevance[:cutoff]
            hit_counts[cutoff] += int(any(selected))
            precision_sums[cutoff] += sum(selected) / cutoff
            ideal_relevant = min(sum(relevance), cutoff)
            ideal_dcg = sum(1 / math.log2(index + 2) for index in range(ideal_relevant))
            dcg = sum(value / math.log2(index + 2) for index, value in enumerate(selected))
            ndcg_sums[cutoff] += dcg / ideal_dcg if ideal_dcg else 0.0
            title_hit_counts[cutoff] += int(
                any(item["title"] in expected_titles for item in results[:cutoff])
            )
        details.append(row)
        category_rows.setdefault(row["category"], []).append(row)
    count = len(cases) or 1
    metrics: dict[str, Any] = {
        "cases": len(cases),
        "mrr": round(reciprocal_rank / count, 4),
        "top1_section_accuracy": round(sum(item["rank"] == 1 for item in details) / count, 4),
        "evidence_recall": round(sum(item["required_recall"] for item in details) / count, 4),
    }
    for cutoff in cutoffs:
        metrics[f"recall@{cutoff}"] = round(hit_counts[cutoff] / count, 4)
        metrics[f"precision@{cutoff}"] = round(precision_sums[cutoff] / count, 4)
        metrics[f"ndcg@{cutoff}"] = round(ndcg_sums[cutoff] / count, 4)
        metrics[f"title_hit@{cutoff}"] = round(title_hit_counts[cutoff] / count, 4)
    metrics["by_category"] = {
        category: {
            "cases": len(rows),
            "recall@5": round(sum(item["rank"] is not None and item["rank"] <= 5 for item in rows) / len(rows), 4),
            "top1_section_accuracy": round(sum(item["rank"] == 1 for item in rows) / len(rows), 4),
            "mrr": round(sum(1 / item["rank"] if item["rank"] else 0 for item in rows) / len(rows), 4),
            "evidence_recall": round(sum(item["required_recall"] for item in rows) / len(rows), 4),
        }
        for category, rows in category_rows.items()
    }
    metrics["failures"] = [item for item in details if item["rank"] is None or item["rank"] > 5]
    metrics["details"] = details
    return metrics
