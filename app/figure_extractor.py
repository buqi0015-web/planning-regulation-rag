from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import fitz

from .config import ROOT
from .pdf_parser import clean_source_url, load_registry


OUTPUT_DIR = ROOT / "data" / "figures"
TARGET_FILE = "P020211108405820326544.pdf"
FIGURE_RE = re.compile(r"图\s*(\d+(?:\.\d+)*(?:~\d+(?:\.\d+)*|-\d+)?)\s*([^\n]*)")


def figure_groups(page: fitz.Page) -> list[dict[str, Any]]:
    matches = list(FIGURE_RE.finditer(page.get_text("text")))
    groups = []
    for index, match in enumerate(matches, start=1):
        label = re.sub(r"\s+", " ", match.group(0)).strip()
        if "地形图" in label or "图为存档数据" in label:
            continue
        groups.append(
            {
                "figure_index": index,
                "label": label,
                "figure_no": match.group(1),
                "caption": match.group(2).strip(),
            }
        )
    return groups


def infer_figure_type(caption: str) -> str:
    if "扫掠角" in caption:
        return "parameter_diagram"
    if "基准面" in caption or "计算起点" in caption:
        return "method_diagram"
    return "technical_illustration"


def extract_figures() -> dict[str, Any]:
    row = next(item for item in load_registry() if item["file_name"] == TARGET_FILE)
    document = fitz.open(row["source_path"])
    image_dir = OUTPUT_DIR / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for page_number, page in enumerate(document, start=1):
        groups = figure_groups(page)
        if not groups:
            continue
        page_text = page.get_text("text")
        for group in groups:
            figure_id = f"{row['sha256'][:16]}-p{page_number:04d}-f{group['figure_index']:02d}"
            image_path = image_dir / f"{figure_id}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False).save(image_path)
            records.append(
                {
                    "figure_id": figure_id,
                    "document_title": row["verified_title"] or row["title_candidate"],
                    "page": page_number,
                    "figure_no": group["figure_no"],
                    "caption": group["caption"],
                    "figure_type": infer_figure_type(group["caption"]),
                    "source_form": "embedded_image" if page.get_images(full=True) else "vector_drawing",
                    "image_path": str(image_path),
                    "context_text": page_text,
                    "source_path": row["source_path"],
                    "source_url": clean_source_url(row["source_url"]),
                    "review_status": "待复核",
                    "visual_summary": "",
                    "review_notes": "",
                }
            )
    document.close()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTPUT_DIR / "figure_manifest.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    review_path = OUTPUT_DIR / "figure_review.csv"
    fields = [
        "figure_id",
        "document_title",
        "page",
        "figure_no",
        "caption",
        "figure_type",
        "source_form",
        "image_path",
        "visual_summary",
        "review_status",
        "review_notes",
    ]
    with review_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: record[key] for key in fields} for record in records)
    summary = {
        "figures": len(records),
        "pages": sorted({record["page"] for record in records}),
        "embedded_images": sum(record["source_form"] == "embedded_image" for record in records),
        "vector_drawings": sum(record["source_form"] == "vector_drawing" for record in records),
        "manifest": str(jsonl_path),
        "review": str(review_path),
        "image_dir": str(image_dir),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
