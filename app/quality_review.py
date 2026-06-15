from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from .config import ROOT


REVIEW_PATH = ROOT / "data" / "parsed" / "quality_review.csv"
MANDATORY_TERMS = ("不得", "严禁", "必须", "禁止", "不应", "应当", "不小于", "不大于")
NUMBER_OR_UNIT_RE = re.compile(r"\d|米|平方米|公顷|百分比|%|层|小时|日照|容积率|高度|距离")


def load_csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(encoding), newline="")))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法识别 CSV 编码：{path}")


def review_priority(row: dict[str, str]) -> tuple[str, str]:
    if row.get("issue_type") == "visual_page":
        return "P2", "视觉页：确认是否包含需要进入知识库的图例或控制指标"
    text = row.get("original_text", "")
    match = re.search(r"置信度\s+([0-9.]+)", row.get("issue", ""))
    confidence = float(match.group(1)) if match else 1.0
    has_mandatory_term = any(term in text for term in MANDATORY_TERMS)
    has_number_or_unit = bool(NUMBER_OR_UNIT_RE.search(text))
    if confidence < 0.72 or (has_mandatory_term and has_number_or_unit):
        return "P0", "极低置信度，或同时包含强制性词语与关键数字"
    if confidence < 0.78 or has_mandatory_term or has_number_or_unit:
        return "P1", "包含数字、管理要求，或 OCR 置信度偏低"
    return "P2", "普通叙述性内容，可快速抽查"


def prioritize_review(path: Path = REVIEW_PATH) -> dict[str, Any]:
    rows = load_csv(path)
    prioritized = []
    counts: dict[str, int] = {}
    for row in rows:
        priority, rationale = review_priority(row)
        output_row = {
            "priority": priority,
            "priority_reason": rationale,
            **row,
        }
        prioritized.append(output_row)
        counts[priority] = counts.get(priority, 0) + 1
    prioritized.sort(key=lambda row: (row["priority"], row["file_name"], int(row["page"])))
    fieldnames = list(prioritized[0]) if prioritized else []
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(prioritized)
    required_rows = []
    p1_index = 0
    for row in prioritized:
        if row["priority"] == "P0" or row.get("issue_type") == "visual_page":
            row["review_scope"] = "必须复核"
            required_rows.append(row)
        elif row["priority"] == "P1":
            p1_index += 1
            if p1_index % 5 == 1:
                row["review_scope"] = "P1抽样复核"
                required_rows.append(row)
    required_path = path.with_name("quality_review_required.csv")
    required_fields = ["review_scope", *fieldnames]
    with required_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=required_fields)
        writer.writeheader()
        writer.writerows(required_rows)
    summary_path = path.with_name("quality_review_summary.json")
    summary = {
        "review_items": len(prioritized),
        "priority_counts": counts,
        "gate_rule": "P0 必须全部通过；P1 必须抽查并通过；P2 可快速确认。",
        "review_path": str(path),
        "required_review_items": len(required_rows),
        "required_review_path": str(required_path),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**summary, "summary_path": str(summary_path)}
