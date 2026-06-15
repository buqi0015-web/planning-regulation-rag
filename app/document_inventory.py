from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import fitz

from .config import ROOT


OUTPUT_DIR = ROOT / "data" / "manifests"
TITLE_NOISE_RE = re.compile(r"^(目\s*录|contents?|第\s*[一二三四五六七八九十\d]+\s*[章节])$", re.I)
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)
PILOT_FILES = {
    "P020211108405820326544.pdf": "试点A：北京市上位规范，直接文本提取",
    "P020231222422229956203.pdf": "试点B：密云区下位规范，混合解析",
    "P020240911614925558614.pdf": "试点C：北京市上位规范，OCR",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def infer_title(metadata_title: str, first_page_text: str, fallback: str) -> str:
    if metadata_title and metadata_title.lower() not in {"untitled", "microsoft word"}:
        return metadata_title.strip()
    candidates = [
        re.sub(r"\s+", " ", line).strip()
        for line in first_page_text.splitlines()
        if 5 <= len(re.sub(r"\s+", "", line)) <= 80
    ]
    for line in candidates:
        if not TITLE_NOISE_RE.match(line) and not line.isdigit():
            return line
    return fallback


def choose_route(
    pages: int,
    text_pages: int,
    image_pages: int,
    avg_chars_per_page: float,
) -> tuple[str, str]:
    if pages == 0:
        return "manual_review", "PDF 无可读取页面"
    text_ratio = text_pages / pages
    image_ratio = image_pages / pages
    if text_ratio >= 0.8 and avg_chars_per_page >= 150:
        return "direct_text", "大部分页面具有可提取文本层"
    if text_ratio <= 0.2 or avg_chars_per_page < 30:
        return "ocr", "文本层缺失或文本量极低，疑似扫描件"
    if image_ratio >= 0.5:
        return "hybrid", "文本页与图片页混合，需要逐页路由"
    return "hybrid", "文本层质量不稳定，需要直接提取与 OCR 混合处理"


def inspect_pdf(path: Path, source_root: Path) -> dict[str, Any]:
    jurisdiction_level = path.relative_to(source_root).parts[0] if path != source_root else ""
    record: dict[str, Any] = {
        "source_path": str(path),
        "source_group": jurisdiction_level,
        "jurisdiction": "北京市" if jurisdiction_level == "北京市上位规范" else "北京市密云区",
        "authority_level": "municipal" if jurisdiction_level == "北京市上位规范" else "district",
        "file_name": path.name,
        "file_size_bytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "status": "待核验",
    }
    try:
        document = fitz.open(path)
        page_chars: list[int] = []
        image_pages = 0
        first_page_text = ""
        for index, page in enumerate(document):
            text = page.get_text("text")
            if index == 0:
                first_page_text = text
            page_chars.append(len(re.sub(r"\s+", "", text)))
            if page.get_images(full=True):
                image_pages += 1
        pages = len(document)
        text_pages = sum(chars >= 30 for chars in page_chars)
        avg_chars = round(sum(page_chars) / pages, 1) if pages else 0.0
        route, reason = choose_route(pages, text_pages, image_pages, avg_chars)
        record.update(
            {
                "title_candidate": infer_title(document.metadata.get("title", ""), first_page_text, path.stem),
                "pages": pages,
                "text_pages": text_pages,
                "image_pages": image_pages,
                "avg_chars_per_page": avg_chars,
                "parse_route": route,
                "route_reason": reason,
                "inspection_error": "",
            }
        )
        document.close()
    except Exception as error:
        record.update(
            {
                "title_candidate": path.stem,
                "pages": 0,
                "text_pages": 0,
                "image_pages": 0,
                "avg_chars_per_page": 0.0,
                "parse_route": "manual_review",
                "route_reason": "PDF 读取失败",
                "inspection_error": f"{type(error).__name__}: {error}",
            }
        )
    return record


def build_inventory(source_root: Path, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    source_root = source_root.resolve()
    records = [inspect_pdf(path, source_root) for path in sorted(source_root.rglob("*.pdf"))]
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "pdf_inventory.jsonl"
    csv_path = output_dir / "pdf_inventory.csv"
    registry_path = output_dir / "document_registry.csv"
    jsonl_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    fieldnames = list(records[0]) if records else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)
    if not registry_path.exists():
        registry_fields = [
            "pilot",
            "file_name",
            "source_group",
            "parse_route",
            "title_candidate",
            "verified_title",
            "document_no",
            "issuing_authority",
            "publish_date",
            "effective_date",
            "status",
            "source_url",
            "superseded_by",
            "review_notes",
            "source_path",
            "sha256",
        ]
        with registry_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=registry_fields)
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        "pilot": PILOT_FILES.get(record["file_name"], ""),
                        "file_name": record["file_name"],
                        "source_group": record["source_group"],
                        "parse_route": record["parse_route"],
                        "title_candidate": record["title_candidate"],
                        "verified_title": "",
                        "document_no": "",
                        "issuing_authority": "",
                        "publish_date": "",
                        "effective_date": "",
                        "status": "待核验",
                        "source_url": "",
                        "superseded_by": "",
                        "review_notes": "",
                        "source_path": record["source_path"],
                        "sha256": record["sha256"],
                    }
                )
    route_counts: dict[str, int] = {}
    for record in records:
        route = record["parse_route"]
        route_counts[route] = route_counts.get(route, 0) + 1
    return {
        "source_root": str(source_root),
        "documents": len(records),
        "route_counts": route_counts,
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
        "registry": str(registry_path),
    }
