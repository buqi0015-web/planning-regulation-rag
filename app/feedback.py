from __future__ import annotations

import json
import sqlite3
from typing import Any

from .core import connect


def ensure_feedback_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            answer TEXT NOT NULL,
            rating TEXT NOT NULL,
            reason TEXT DEFAULT '',
            citations_json TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def save_feedback(
    query: str,
    answer: str,
    rating: str,
    reason: str = "",
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if rating not in {"helpful", "bad"}:
        raise ValueError("rating must be helpful or bad")
    conn = connect()
    try:
        ensure_feedback_table(conn)
        cursor = conn.execute(
            """
            INSERT INTO feedback(query, answer, rating, reason, citations_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                query,
                answer,
                rating,
                reason,
                json.dumps(citations or [], ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"status": "ok", "feedback_id": cursor.lastrowid}
    finally:
        conn.close()
