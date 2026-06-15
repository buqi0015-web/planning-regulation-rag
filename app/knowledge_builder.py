from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import fitz

from .config import ROOT
from .pdf_parser import _yaml_value, clean_text, load_registry, normalize_date, clean_source_url
from .quality_review import load_csv


TABLE_DIR = ROOT / "data" / "tables"
FIGURE_DIR = ROOT / "data" / "figures"
OUTPUT_DIR = ROOT / "data" / "knowledge"
CORRECTIONS_PATH = TABLE_DIR / "verified_table_corrections.json"


def _load_manifest() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (TABLE_DIR / "table_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _clean_page_without_tables(page: fitz.Page, tables: list[dict[str, Any]]) -> str:
    table_rects = [fitz.Rect(table["bbox"]) for table in tables]
    kept = []
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        text = clean_text(block[4])
        if not text:
            continue
        overlap = max(
            ((rect & table_rect).get_area() / (rect.get_area() or 1) for table_rect in table_rects),
            default=0,
        )
        if overlap < 0.35:
            kept.append((rect.y0, rect.x0, text))
    kept.sort()
    text = clean_text("\n".join(item[2] for item in kept))
    references = "\n".join(
        f"[表格引用：{table['table_title']}，PDF第{table['page']}页，table_id={table['table_id']}]"
        for table in tables
    )
    return clean_text(f"{text}\n\n{references}")


def build_verified_knowledge() -> dict[str, Any]:
    manifest = _load_manifest()
    review_rows = {row["table_id"]: row for row in load_csv(TABLE_DIR / "table_review.csv")}
    corrections = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))
    registry_row = next(
        row for row in load_registry() if row["file_name"] == "P020211108405820326544.pdf"
    )
    verified_tables = []
    for table in manifest:
        review = review_rows.get(table["table_id"], {})
        correction = corrections.get(table["table_id"], {})
        corrected_markdown = review.get("corrected_markdown", "").strip() or correction.get("markdown", "")
        if review.get("review_status") != "已通过":
            raise ValueError(f"表格尚未通过复核：{table['table_id']}")
        if table["needs_review"] and not corrected_markdown:
            raise ValueError(f"污染表格已通过但缺少修正版：{table['table_id']}")
        table["table_title"] = correction.get("table_title", table["table_title"])
        table["verified_markdown"] = corrected_markdown or table["markdown"]
        verified_tables.append(table)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    table_output_dir = OUTPUT_DIR / "tables"
    table_output_dir.mkdir(parents=True, exist_ok=True)
    for table in verified_tables:
        metadata = {
            "title": f"{table['document_title']} - {table['table_title']}",
            "content_type": "table",
            "parent_document": table["document_title"],
            "table_id": table["table_id"],
            "page": table["page"],
            "status": registry_row["status"],
            "jurisdiction": "北京市",
            "source_url": clean_source_url(registry_row["source_url"]),
            "source_file": registry_row["source_path"],
        }
        frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
        (table_output_dir / f"{table['table_id']}.md").write_text(
            f"---\n{frontmatter}\n---\n\n# {table['table_title']}\n\n"
            f"{table['verified_markdown']}\n",
            encoding="utf-8",
        )

    figure_output_dir = OUTPUT_DIR / "figures"
    figure_output_dir.mkdir(parents=True, exist_ok=True)
    figure_summaries = json.loads(
        (FIGURE_DIR / "verified_figure_summaries.json").read_text(encoding="utf-8")
    )
    figure_manifest = [
        json.loads(line)
        for line in (FIGURE_DIR / "figure_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    figure_documents = []
    for figure in figure_manifest:
        verified = figure_summaries.get(figure["figure_id"])
        if not verified:
            raise ValueError(f"图示缺少已复核摘要：{figure['figure_id']}")
        metadata = {
            "title": f"{figure['document_title']} - 图{verified['figure_no']}",
            "content_type": "figure",
            "parent_document": figure["document_title"],
            "figure_id": figure["figure_id"],
            "figure_no": verified["figure_no"],
            "related_clause": verified["related_clause"],
            "page": figure["page"],
            "status": registry_row["status"],
            "jurisdiction": "北京市",
            "source_url": clean_source_url(registry_row["source_url"]),
            "source_file": registry_row["source_path"],
            "image_path": figure["image_path"],
        }
        frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
        output_path = figure_output_dir / f"{figure['figure_id']}.md"
        output_path.write_text(
            f"---\n{frontmatter}\n---\n\n# 图{verified['figure_no']} {figure['caption']}\n\n"
            f"{verified['summary']}\n\n"
            f"[关联条款：{verified['related_clause']}；原图：PDF第{figure['page']}页]\n",
            encoding="utf-8",
        )
        figure_documents.append(str(output_path))

    tables_by_page: dict[int, list[dict[str, Any]]] = {}
    for table in verified_tables:
        tables_by_page.setdefault(table["page"], []).append(table)
    document = fitz.open(registry_row["source_path"])
    page_parts = []
    for index, page in enumerate(document, start=1):
        tables = tables_by_page.get(index, [])
        if tables:
            text = _clean_page_without_tables(page, tables)
        else:
            text = clean_text(page.get_text("text"))
        if text:
            page_parts.append(f"## PDF 第 {index} 页\n\n{text}")
    document.close()

    title = registry_row["verified_title"]
    metadata = {
        "title": title,
        "document_type": "标准规范",
        "content_type": "body",
        "document_no": registry_row["document_no"],
        "authority": registry_row["issuing_authority"],
        "jurisdiction": "北京市",
        "publish_date": normalize_date(registry_row["publish_date"]),
        "effective_date": normalize_date(registry_row["effective_date"]),
        "status": registry_row["status"],
        "source_url": clean_source_url(registry_row["source_url"]),
        "source_file": registry_row["source_path"],
        "source_sha256": registry_row["sha256"],
    }
    frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
    body_path = OUTPUT_DIR / f"{re.sub(r'[<>:\"/\\\\|?*]+', '_', title)}.md"
    body_path.write_text(
        f"---\n{frontmatter}\n---\n\n" + "\n\n".join(page_parts) + "\n",
        encoding="utf-8",
    )
    return {
        "body_document": str(body_path),
        "verified_tables": len(verified_tables),
        "table_documents": [str(table_output_dir / f"{table['table_id']}.md") for table in verified_tables],
        "verified_figures": len(figure_documents),
        "figure_documents": figure_documents,
    }
