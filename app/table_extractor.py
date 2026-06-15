from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import fitz

from .config import ROOT
from .pdf_parser import load_registry


OUTPUT_DIR = ROOT / "data" / "tables"
SINGLE_CHAR_LINE_RE = re.compile(r"(?m)^\s*[\u4e00-\u9fff]\s*$")


def clean_cell(value: str | None) -> str:
    if not value:
        return ""
    lines = []
    for line in value.replace("\r", "").splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line and line not in lines:
            lines.append(line)
    return "\n".join(lines)


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]

    def cell(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", "<br>")

    header = "| " + " | ".join(cell(value) for value in padded[0]) + " |"
    separator = "| " + " | ".join("---" for _ in range(width)) + " |"
    body = ["| " + " | ".join(cell(value) for value in row) + " |" for row in padded[1:]]
    return "\n".join([header, separator, *body])


def infer_table_title(page: fitz.Page, bbox: tuple[float, float, float, float], index: int) -> str:
    x0, y0, x1, _ = bbox
    clip = fitz.Rect(max(0, x0 - 30), max(0, y0 - 80), min(page.rect.width, x1 + 30), y0)
    lines = [line.strip() for line in page.get_text("text", clip=clip).splitlines() if line.strip()]
    for line in reversed(lines):
        if re.search(r"表\s*\d+(?:\.\d+)*", line):
            return re.sub(r"\s+", " ", line)
    return f"PDF第{page.number + 1}页表格{index}"


def table_quality(rows: list[list[str]]) -> tuple[bool, list[str]]:
    reasons = []
    text = "\n".join(cell for row in rows for cell in row)
    chars = len(re.sub(r"\s+", "", text))
    non_empty = sum(bool(cell.strip()) for row in rows for cell in row)
    total = sum(len(row) for row in rows) or 1
    if chars < 30 or non_empty / total < 0.35:
        reasons.append("内容过少，疑似版面线条或水印误识别")
    if SINGLE_CHAR_LINE_RE.search(text):
        reasons.append("单元格中存在孤立单字，疑似水印污染")
    return bool(reasons), reasons


def extract_document_tables(row: dict[str, str]) -> list[dict[str, Any]]:
    document = fitz.open(row["source_path"])
    records = []
    for page_index, page in enumerate(document):
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        for table_index, table in enumerate(tables, start=1):
            rows = [[clean_cell(value) for value in raw_row] for raw_row in table.extract()]
            needs_review, reasons = table_quality(rows)
            if "内容过少，疑似版面线条或水印误识别" in reasons:
                continue
            title = infer_table_title(page, table.bbox, table_index)
            records.append(
                {
                    "table_id": f"{row['sha256'][:16]}-p{page_index + 1:04d}-t{table_index:02d}",
                    "document_title": row["verified_title"] or row["title_candidate"],
                    "file_name": row["file_name"],
                    "page": page_index + 1,
                    "table_title": title,
                    "bbox": [round(value, 2) for value in table.bbox],
                    "rows": table.row_count,
                    "columns": table.col_count,
                    "cells": rows,
                    "markdown": markdown_table(rows),
                    "needs_review": needs_review,
                    "quality_reasons": reasons,
                    "source_path": row["source_path"],
                    "source_url": row["source_url"],
                }
            )
    document.close()
    return records


def extract_pilot_tables() -> dict[str, Any]:
    rows = [row for row in load_registry() if row["file_name"] == "P020211108405820326544.pdf"]
    records = [record for row in rows for record in extract_document_tables(row)]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTPUT_DIR / "table_manifest.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    review_path = OUTPUT_DIR / "table_review.csv"
    fields = [
        "table_id",
        "document_title",
        "page",
        "table_title",
        "rows",
        "columns",
        "needs_review",
        "quality_reasons",
        "preview_path",
        "original_markdown",
        "corrected_markdown",
        "review_status",
        "review_notes",
    ]
    preview_dir = OUTPUT_DIR / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    source_documents: dict[str, fitz.Document] = {}
    with review_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            source_path = record["source_path"]
            if source_path not in source_documents:
                source_documents[source_path] = fitz.open(source_path)
            page = source_documents[source_path][record["page"] - 1]
            preview_path = preview_dir / f"{record['table_id']}.png"
            clip = fitz.Rect(record["bbox"]) + (-10, -25, 10, 10)
            page.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), clip=clip, alpha=False).save(preview_path)
            writer.writerow(
                {
                    "table_id": record["table_id"],
                    "document_title": record["document_title"],
                    "page": record["page"],
                    "table_title": record["table_title"],
                    "rows": record["rows"],
                    "columns": record["columns"],
                    "needs_review": record["needs_review"],
                    "quality_reasons": "；".join(record["quality_reasons"]),
                    "preview_path": str(preview_path),
                    "original_markdown": record["markdown"],
                    "corrected_markdown": "",
                    "review_status": "待复核",
                    "review_notes": "",
                }
            )
    for document in source_documents.values():
        document.close()
    for record in records:
        output_path = OUTPUT_DIR / f"{record['table_id']}.md"
        output_path.write_text(
            f"# {record['table_title']}\n\n"
            f"- 来源：{record['document_title']}\n"
            f"- PDF 页码：{record['page']}\n"
            f"- 表格 ID：`{record['table_id']}`\n"
            f"- 需要复核：{record['needs_review']}\n"
            f"- 质量告警：{'；'.join(record['quality_reasons']) or '无'}\n\n"
            f"{record['markdown']}\n",
            encoding="utf-8",
        )
    return {
        "tables": len(records),
        "needs_review": sum(record["needs_review"] for record in records),
        "manifest": str(jsonl_path),
        "review": str(review_path),
        "output_dir": str(OUTPUT_DIR),
    }
