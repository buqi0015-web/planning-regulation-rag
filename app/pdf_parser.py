from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import fitz

from .config import ROOT
from .document_inventory import PILOT_FILES


REGISTRY_PATH = ROOT / "data" / "manifests" / "document_registry.csv"
OUTPUT_DIR = ROOT / "data" / "parsed"
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)
_OCR_ENGINE: Any | None = None


def clean_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_repeated_page_noise(pages: list[dict[str, Any]]) -> None:
    line_pages: dict[str, set[int]] = {}
    for page in pages:
        for line in set(page["text"].splitlines()):
            normalized = re.sub(r"\s+", "", line)
            if 1 <= len(normalized) <= 60:
                line_pages.setdefault(normalized, set()).add(page["page"])
    threshold = max(3, math.ceil(len(pages) * 0.6))
    repeated = {line for line, page_numbers in line_pages.items() if len(page_numbers) >= threshold}
    for page in pages:
        kept = []
        seen: set[str] = set()
        for line in page["text"].splitlines():
            normalized = re.sub(r"\s+", "", line)
            if normalized in repeated or normalized in seen:
                continue
            seen.add(normalized)
            kept.append(line)
        page["text"] = clean_text("\n".join(kept))
        page["chars"] = len(re.sub(r"\s+", "", page["text"]))


def normalize_date(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    for pattern in ("%Y/%m/%d", "%Y-%m-%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def clean_source_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def infer_document_type(title: str, document_no: str) -> str:
    if "通知" in title:
        return "政策通知"
    if "规划" in title:
        return "规划成果"
    if re.search(r"DB\d|DB11", document_no) or "标准" in title or "规范" in title:
        return "标准规范"
    return "其他政策文件"


def load_registry(registry_path: Path = REGISTRY_PATH) -> list[dict[str, str]]:
    raw = registry_path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            return list(csv.DictReader(io.StringIO(text, newline="")))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"无法识别登记表编码：{registry_path}")


def _ocr_page(page: fitz.Page) -> tuple[str, float | None]:
    global _OCR_ENGINE
    from rapidocr_onnxruntime import RapidOCR

    if _OCR_ENGINE is None:
        _OCR_ENGINE = RapidOCR()
    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), alpha=False)
    image_bytes = pixmap.tobytes("png")
    result, _ = _OCR_ENGINE(image_bytes)
    if not result:
        return "", None
    lines = [str(item[1]) for item in result if len(item) >= 3]
    scores = [float(item[2]) for item in result if len(item) >= 3]
    return "\n".join(lines), round(sum(scores) / len(scores), 4) if scores else None


def parse_document(row: dict[str, str], max_pages: int = 5) -> dict[str, Any]:
    path = Path(row["source_path"])
    document = fitz.open(path)
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(document):
        if index >= max_pages:
            break
        direct_text = clean_text(page.get_text("text"))
        route = row["parse_route"]
        use_ocr = route == "ocr" or (route == "hybrid" and len(re.sub(r"\s+", "", direct_text)) < 30)
        if use_ocr:
            text, ocr_confidence = _ocr_page(page)
            method = "ocr"
        else:
            text, ocr_confidence = direct_text, None
            method = "direct_text"
        pages.append(
            {
                "page": index + 1,
                "method": method,
                "chars": len(re.sub(r"\s+", "", text)),
                "ocr_confidence": ocr_confidence,
                "text": clean_text(text),
            }
        )
    document.close()
    remove_repeated_page_noise(pages)

    title = row["verified_title"].strip() or row["title_candidate"].strip() or path.stem
    metadata = {
        "title": title,
        "document_type": infer_document_type(title, row["document_no"]),
        "document_no": row["document_no"].strip(),
        "authority": row["issuing_authority"].strip(),
        "jurisdiction": "北京市" if row["source_group"] == "北京市上位规范" else "北京市密云区",
        "authority_level": "municipal" if row["source_group"] == "北京市上位规范" else "district",
        "publish_date": normalize_date(row["publish_date"]),
        "effective_date": normalize_date(row["effective_date"]),
        "status": row["status"].strip() or "待核验",
        "source_url": clean_source_url(row["source_url"]),
        "source_file": str(path),
        "source_sha256": row["sha256"],
        "parse_route": row["parse_route"],
        "identifier": row["document_no"].strip() or row["sha256"][:16],
    }
    return {"metadata": metadata, "pages": pages}


def _yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_parsed_document(parsed: dict[str, Any], output_dir: Path = OUTPUT_DIR) -> Path:
    metadata = parsed["metadata"]
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", metadata["title"]).strip(" .")[:100]
    output_path = output_dir / f"{safe_name}.md"
    frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
    body_parts = []
    for page in parsed["pages"]:
        confidence = (
            f", OCR平均置信度 {page['ocr_confidence']}"
            if page["ocr_confidence"] is not None
            else ""
        )
        body_parts.append(
            f"## PDF 第 {page['page']} 页\n\n"
            f"<!-- parse_method: {page['method']}, chars: {page['chars']}{confidence} -->\n\n"
            f"{page['text']}"
        )
    output_path.write_text(
        f"---\n{frontmatter}\n---\n\n" + "\n\n".join(body_parts) + "\n",
        encoding="utf-8",
    )
    return output_path


def parse_pilots(max_pages: int = 5) -> dict[str, Any]:
    rows = [row for row in load_registry() if row["file_name"] in PILOT_FILES]
    results = []
    review_rows = []
    for row in rows:
        parsed = parse_document(row, max_pages=max_pages)
        output_path = write_parsed_document(parsed)
        methods: dict[str, int] = {}
        warnings = []
        for page in parsed["pages"]:
            methods[page["method"]] = methods.get(page["method"], 0) + 1
            if page["chars"] == 0:
                issue = f"第 {page['page']} 页无可检索文本，作为视觉页保留"
                warnings.append(issue)
                review_rows.append(
                    {
                        "file_name": row["file_name"],
                        "title": parsed["metadata"]["title"],
                        "page": page["page"],
                        "issue_type": "visual_page",
                        "issue": issue,
                        "original_text": "",
                        "corrected_text": "",
                        "review_status": "待复核",
                        "review_notes": "",
                    }
                )
            if page["ocr_confidence"] is not None and page["ocr_confidence"] < 0.85:
                issue = f"第 {page['page']} 页 OCR 平均置信度 {page['ocr_confidence']}，需要抽查"
                warnings.append(issue)
                review_rows.append(
                    {
                        "file_name": row["file_name"],
                        "title": parsed["metadata"]["title"],
                        "page": page["page"],
                        "issue_type": "low_ocr_confidence",
                        "issue": issue,
                        "original_text": page["text"],
                        "corrected_text": "",
                        "review_status": "待复核",
                        "review_notes": "",
                    }
                )
        results.append(
            {
                "file_name": row["file_name"],
                "title": parsed["metadata"]["title"],
                "document_type": parsed["metadata"]["document_type"],
                "identifier": parsed["metadata"]["identifier"],
                "pages_parsed": len(parsed["pages"]),
                "methods": methods,
                "total_chars": sum(page["chars"] for page in parsed["pages"]),
                "warnings": warnings,
                "output": str(output_path),
            }
        )
    report_path = OUTPUT_DIR / "pilot_parse_report.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    review_path = OUTPUT_DIR / "quality_review.csv"
    review_fields = [
        "file_name",
        "title",
        "page",
        "issue_type",
        "issue",
        "original_text",
        "corrected_text",
        "review_status",
        "review_notes",
    ]
    with review_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=review_fields)
        writer.writeheader()
        writer.writerows(review_rows)
    return {
        "documents": len(results),
        "max_pages": max_pages,
        "report": str(report_path),
        "quality_review": str(review_path),
        "review_items": len(review_rows),
        "results": results,
    }
