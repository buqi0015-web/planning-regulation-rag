from __future__ import annotations

import os
from typing import Any


class BaseReranker:
    name = "none"

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return candidates


class RuleBasedReranker(BaseReranker):
    name = "rule"

    def rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query_chars = set(query)

        def score(item: dict[str, Any]) -> float:
            content = f"{item.get('title', '')}{item.get('section', '')}{item.get('content', '')}"
            overlap = len(query_chars.intersection(content)) / (len(query_chars) or 1)
            return float(item.get("score", 0.0)) + overlap * 0.05

        reranked = []
        for item in candidates:
            copied = dict(item)
            copied["reranker_score"] = round(score(item), 6)
            reranked.append(copied)
        return sorted(reranked, key=lambda item: item["reranker_score"], reverse=True)


def get_reranker() -> BaseReranker:
    provider = os.getenv("RERANKER_PROVIDER", "none").lower()
    if provider == "rule":
        return RuleBasedReranker()
    return BaseReranker()
