from __future__ import annotations

import json
import re
from os import PathLike
from pathlib import Path
from typing import Any

from .core import ROOT, ask


def _has_citation(answer: str) -> bool:
    return bool(re.search(r"\[\d+\]", answer))


def _is_abstention(answer: str) -> bool:
    return any(term in answer for term in ("没有找到", "证据不足", "无法确定", "不能判断", "未收录"))


def evaluate_answers(dataset_path: str | PathLike[str] | Path, top_k: int = 3) -> dict[str, Any]:
    dataset_path = Path(dataset_path)
    cases = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    citation_ok = 0
    abstention_ok = 0
    for case in cases:
        result = ask(case["query"], top_k=top_k, filters=case.get("filters"))
        answer = result["answer"]
        expected_abstain = bool(case.get("should_abstain", False))
        row = {
            "query": case["query"],
            "category": case.get("category", "uncategorized"),
            "must_cite": bool(case.get("must_cite", True)),
            "should_abstain": expected_abstain,
            "has_citation": _has_citation(answer),
            "is_abstention": _is_abstention(answer),
            "answer": answer,
        }
        if not row["must_cite"] or row["has_citation"]:
            citation_ok += 1
        if row["is_abstention"] == expected_abstain:
            abstention_ok += 1
        rows.append(row)
    count = len(rows) or 1
    return {
        "cases": len(rows),
        "citation_format_rate": round(citation_ok / count, 4),
        "abstention_accuracy": round(abstention_ok / count, 4),
        "details": rows,
        "note": "该评测仅检查引用格式与拒答行为，正式回答正确率仍需人工或专家复核。",
    }


def default_answer_eval_path() -> Path:
    return ROOT / "data" / "eval" / "answer_quality_v1.jsonl"
