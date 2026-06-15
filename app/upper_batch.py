from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

import fitz

from .config import ROOT
from .pdf_parser import (
    _yaml_value,
    clean_source_url,
    clean_text,
    infer_document_type,
    load_registry,
    normalize_date,
    parse_document,
    write_parsed_document,
)
from .table_extractor import clean_cell, extract_document_tables, markdown_table, table_quality


OUTPUT_DIR = ROOT / "data" / "staging" / "beijing-upper"
KNOWLEDGE_DIR = ROOT / "data" / "knowledge"
FIGURE_LABEL_RE = re.compile(r"图\s*\d+(?:\.\d+)*(?:~\d+(?:\.\d+)*|-\d+)?\s*[^\n]*")
TABLE_HEADER_TERMS = (
    "序号", "类别", "项目", "指标", "名称", "标准", "单位", "类型", "等级",
    "建筑面积", "用地面积", "容积率", "高度", "距离", "数量", "配置",
)


def upper_rows() -> list[dict[str, str]]:
    return [row for row in load_registry() if row.get("source_group") == "北京市上位规范"]


def _quality_rows(row: dict[str, str], parsed: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for page in parsed["pages"]:
        if page["chars"] == 0:
            records.append(
                {
                    "file_name": row["file_name"],
                    "title": parsed["metadata"]["title"],
                    "page": page["page"],
                    "issue_type": "visual_page",
                    "issue": "无可检索文本，需要确认是否为封面、空白页或技术图示",
                    "ocr_confidence": "",
                    "review_status": "待复核",
                    "review_notes": "",
                }
            )
        if page["ocr_confidence"] is not None and page["ocr_confidence"] < 0.85:
            records.append(
                {
                    "file_name": row["file_name"],
                    "title": parsed["metadata"]["title"],
                    "page": page["page"],
                    "issue_type": "low_ocr_confidence",
                    "issue": "OCR 平均置信度低于 0.85",
                    "ocr_confidence": page["ocr_confidence"],
                    "review_status": "待复核",
                    "review_notes": "",
                }
            )
    return records


def _audit_tables_and_figures(row: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    document = fitz.open(row["source_path"])
    tables = []
    figures = []
    preview_dir = OUTPUT_DIR / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    for page_number, page in enumerate(document, start=1):
        try:
            found_tables = page.find_tables(strategy="lines").tables
        except Exception:
            found_tables = []
        for index, table in enumerate(found_tables, start=1):
            cells = [[clean_cell(value) for value in raw_row] for raw_row in table.extract()]
            needs_review, reasons = table_quality(cells)
            if "内容过少，疑似版面线条或水印误识别" in reasons:
                continue
            candidate_id = f"{row['sha256'][:16]}-p{page_number:04d}-t{index:02d}"
            preview_path = preview_dir / f"{candidate_id}.png"
            clip = fitz.Rect(table.bbox) + (-10, -25, 10, 10)
            page.get_pixmap(matrix=fitz.Matrix(1.8, 1.8), clip=clip, alpha=False).save(preview_path)
            tables.append(
                {
                    "candidate_id": candidate_id,
                    "file_name": row["file_name"],
                    "document_title": row["verified_title"] or row["title_candidate"],
                    "page": page_number,
                    "rows": table.row_count,
                    "columns": table.col_count,
                    "needs_review": needs_review,
                    "quality_reasons": "；".join(reasons),
                    "original_markdown": markdown_table(cells),
                    "preview_path": str(preview_path),
                    "review_status": "待复核",
                    "review_notes": "",
                }
            )
        text = page.get_text("text")
        labels = [re.sub(r"\s+", " ", label).strip() for label in FIGURE_LABEL_RE.findall(text)]
        if labels:
            figure_id = f"{row['sha256'][:16]}-p{page_number:04d}"
            preview_path = preview_dir / f"{figure_id}-figure-page.png"
            page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).save(preview_path)
            figures.append(
                {
                    "candidate_id": figure_id,
                    "file_name": row["file_name"],
                    "document_title": row["verified_title"] or row["title_candidate"],
                    "page": page_number,
                    "labels": "；".join(labels),
                    "source_form": "embedded_image" if page.get_images(full=True) else "vector_drawing",
                    "preview_path": str(preview_path),
                    "review_status": "待复核",
                    "review_notes": "",
                }
            )
    document.close()
    return tables, figures


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def process_upper_documents(route: str = "all") -> dict[str, Any]:
    selected = [row for row in upper_rows() if route == "all" or row["parse_route"] == route]
    parsed_dir = OUTPUT_DIR / "parsed"
    reports = []
    quality = []
    tables = []
    figures = []
    for row in selected:
        parsed = parse_document(row, max_pages=100000)
        output_path = write_parsed_document(parsed, output_dir=parsed_dir)
        table_candidates, figure_candidates = _audit_tables_and_figures(row)
        quality.extend(_quality_rows(row, parsed))
        tables.extend(table_candidates)
        figures.extend(figure_candidates)
        reports.append(
            {
                "file_name": row["file_name"],
                "title": parsed["metadata"]["title"],
                "parse_route": row["parse_route"],
                "metadata_status": row["status"],
                "eligible_for_knowledge": row["status"] == "现行有效" and bool(row["verified_title"]),
                "pages": len(parsed["pages"]),
                "chars": sum(page["chars"] for page in parsed["pages"]),
                "tables": len(table_candidates),
                "figure_pages": len(figure_candidates),
                "quality_issues": len(_quality_rows(row, parsed)),
                "output": str(output_path),
            }
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / f"processing_report_{route}.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(
        OUTPUT_DIR / f"quality_review_{route}.csv",
        quality,
        ["file_name", "title", "page", "issue_type", "issue", "ocr_confidence", "review_status", "review_notes"],
    )
    _write_csv(
        OUTPUT_DIR / f"table_candidates_{route}.csv",
        tables,
        [
            "candidate_id", "file_name", "document_title", "page", "rows", "columns",
            "needs_review", "quality_reasons", "original_markdown", "preview_path",
            "review_status", "review_notes",
        ],
    )
    _write_csv(
        OUTPUT_DIR / f"figure_candidates_{route}.csv",
        figures,
        [
            "candidate_id", "file_name", "document_title", "page", "labels", "source_form",
            "preview_path", "review_status", "review_notes",
        ],
    )
    gate_rows = [
        {
            "file_name": row["file_name"],
            "parse_route": row["parse_route"],
            "verified_title": row["verified_title"],
            "status": row["status"],
            "source_url": row["source_url"],
            "gate_result": (
                "允许进入正式知识层"
                if row["status"] == "现行有效" and row["verified_title"] and row["source_url"]
                else "暂存层：需要核验名称、效力状态和官方来源"
            ),
        }
        for row in selected
    ]
    _write_csv(
        OUTPUT_DIR / f"metadata_gate_{route}.csv",
        gate_rows,
        ["file_name", "parse_route", "verified_title", "status", "source_url", "gate_result"],
    )
    return {
        "route": route,
        "documents": len(reports),
        "pages": sum(report["pages"] for report in reports),
        "quality_issues": len(quality),
        "table_candidates": len(tables),
        "figure_candidate_pages": len(figures),
        "eligible_for_knowledge": sum(report["eligible_for_knowledge"] for report in reports),
        "output_dir": str(OUTPUT_DIR),
    }


def _better_title(report: dict[str, Any]) -> str:
    title = report["title"].strip()
    if title and not title.startswith("<") and title != report["file_name"].replace(".pdf", ""):
        return title
    parsed_path = Path(report["output"])
    text = parsed_path.read_text(encoding="utf-8")[:12000]
    candidates = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        compact = re.sub(r"\s+", "", line)
        if 5 <= len(compact) <= 60 and any(
            word in compact for word in ("标准", "规范", "规程", "通知", "办法", "规划", "条例", "规定")
        ):
            candidates.append(line)
    return candidates[0] if candidates else title


def summarize_upper_processing() -> dict[str, Any]:
    reports = []
    for route in ("direct_text", "ocr"):
        path = OUTPUT_DIR / f"processing_report_{route}.json"
        if path.exists():
            reports.extend(json.loads(path.read_text(encoding="utf-8")))
    registry = {row["file_name"]: row for row in upper_rows()}
    review_rows = []
    for report in reports:
        row = registry[report["file_name"]]
        metadata_complete = bool(row["verified_title"] and row["status"])
        citation_complete = bool(row["source_url"])
        knowledge_ready = any(
            path.stem == row["verified_title"]
            for path in (ROOT / "data" / "knowledge").glob("*.md")
        ) if row["verified_title"] else False
        if knowledge_ready:
            priority = "已完成"
            next_action = "保持正式知识层，后续按更新机制维护"
        elif metadata_complete:
            priority = "P0"
            next_action = (
                "元数据已核验；完成解析质量、表格和图示复核后进入正式知识层"
                + ("；官方来源链接待补" if not citation_complete else "")
            )
        elif report["parse_route"] == "direct_text" and (
            "DB11" in report["title"] or "标准" in _better_title(report) or "规范" in _better_title(report)
        ):
            priority = "P0"
            next_action = "优先核验正式名称、标准号/文号、效力状态和官方来源"
        elif report["parse_route"] == "ocr":
            priority = "P1"
            next_action = "先核验元数据，再处理高风险 OCR 页和扫描表格"
        else:
            priority = "P1"
            next_action = "核验元数据后复核表格与图示候选"
        review_rows.append(
            {
                "priority": priority,
                "file_name": report["file_name"],
                "title_candidate": _better_title(report),
                "parse_route": report["parse_route"],
                "pages": report["pages"],
                "table_candidates": report["tables"],
                "figure_candidate_pages": report["figure_pages"],
                "quality_issues": report["quality_issues"],
                "verified_title": row["verified_title"],
                "document_no": row["document_no"],
                "status": row["status"],
                "source_url": row["source_url"],
                "metadata_gate": "已通过" if metadata_complete else "待核验",
                "citation_gate": "已通过" if citation_complete else "官方来源链接待补",
                "knowledge_ready": "是" if knowledge_ready else "否",
                "next_action": next_action,
            }
        )
    review_rows.sort(key=lambda item: (item["priority"], -item["table_candidates"], item["file_name"]))
    review_path = OUTPUT_DIR / "metadata_review_required.csv"
    fields = list(review_rows[0]) if review_rows else []
    _write_csv(review_path, review_rows, fields)
    summary = {
        "documents": len(reports),
        "pages": sum(item["pages"] for item in reports),
        "direct_text_documents": sum(item["parse_route"] == "direct_text" for item in reports),
        "ocr_documents": sum(item["parse_route"] == "ocr" for item in reports),
        "metadata_gate_passed": sum(item["metadata_gate"] == "已通过" for item in review_rows),
        "citation_gate_passed": sum(item["citation_gate"] == "已通过" for item in review_rows),
        "knowledge_ready": sum(item["knowledge_ready"] == "是" for item in review_rows),
        "pending_metadata_review": sum(item["metadata_gate"] != "已通过" for item in review_rows),
        "table_candidates": sum(item["tables"] for item in reports),
        "figure_candidate_pages": sum(item["figure_pages"] for item in reports),
        "quality_issues": sum(item["quality_issues"] for item in reports),
        "metadata_review": str(review_path),
    }
    report_path = OUTPUT_DIR / "upper_processing_report.md"
    report_path.write_text(
        "# 北京市上位规范批处理报告\n\n"
        f"- 文档：{summary['documents']} 份\n"
        f"- 页数：{summary['pages']} 页\n"
        f"- 直接文本解析：{summary['direct_text_documents']} 份\n"
        f"- OCR 解析：{summary['ocr_documents']} 份\n"
        f"- 已通过元数据门禁：{summary['metadata_gate_passed']} 份\n"
        f"- 已通过官方来源引用门禁：{summary['citation_gate_passed']} 份\n"
        f"- 已完成全部质量门禁并进入正式知识层：{summary['knowledge_ready']} 份\n"
        f"- 等待元数据核验：{summary['pending_metadata_review']} 份\n"
        f"- 表格候选：{summary['table_candidates']} 个\n"
        f"- 图示候选页：{summary['figure_candidate_pages']} 页\n"
        f"- 解析质量告警：{summary['quality_issues']} 条\n\n"
        "## 质量门禁\n\n"
        "所有文档均已进入暂存解析层，但只有完成正式名称、效力状态和官方来源核验后，"
        "才允许进入正式知识层。表格候选不等于真实表格，必须排除正文、目录和工程图线框误报。"
        "扫描件的表格和图示需要使用 PP-StructureV3 或视觉模型处理。\n\n"
        "## 下一步\n\n"
        "先完成 `metadata_review_required.csv` 中 P0 文档的元数据核验，"
        "再按文档逐份生成表格、图示和质量复核任务。不要直接将全部暂存 Markdown 入库。\n",
        encoding="utf-8",
    )
    summary["report"] = str(report_path)
    return summary


def triage_upper_candidates() -> dict[str, Any]:
    table_rows = []
    table_path = OUTPUT_DIR / "table_candidates_direct_text.csv"
    if table_path.exists():
        from .quality_review import load_csv

        for row in load_csv(table_path):
            markdown = row["original_markdown"]
            header_hits = sum(term in markdown for term in TABLE_HEADER_TERMS)
            rows = int(row["rows"])
            columns = int(row["columns"])
            if row["needs_review"].lower() == "false" and rows >= 2 and columns >= 2 and header_hits >= 2:
                priority = "P0"
                rationale = "二维结构完整，且命中多个常见表头字段"
            elif rows >= 3 and columns >= 3 and header_hits >= 1:
                priority = "P1"
                rationale = "具有一定二维结构和表头语义，需要抽查"
            else:
                priority = "P2"
                rationale = "可能是目录、正文排版、工程图线框或水印误报"
            table_rows.append({"priority": priority, "triage_reason": rationale, **row})
    table_rows.sort(key=lambda item: (item["priority"], item["file_name"], int(item["page"])))
    table_review_path = OUTPUT_DIR / "table_review_required.csv"
    table_fields = list(table_rows[0]) if table_rows else []
    _write_csv(table_review_path, table_rows, table_fields)

    figure_rows = []
    figure_path = OUTPUT_DIR / "figure_candidates_direct_text.csv"
    if figure_path.exists():
        from .quality_review import load_csv

        for row in load_csv(figure_path):
            labels = row["labels"]
            priority = "P0" if any(term in labels for term in ("指标", "示意", "控制", "分区", "布局")) else "P1"
            figure_rows.append({"priority": priority, **row})
    figure_rows.sort(key=lambda item: (item["priority"], item["file_name"], int(item["page"])))
    figure_review_path = OUTPUT_DIR / "figure_review_required.csv"
    figure_fields = list(figure_rows[0]) if figure_rows else []
    _write_csv(figure_review_path, figure_rows, figure_fields)

    summary = {
        "table_candidates": len(table_rows),
        "table_priority_counts": {
            priority: sum(item["priority"] == priority for item in table_rows)
            for priority in ("P0", "P1", "P2")
        },
        "figure_candidate_pages": len(figure_rows),
        "figure_priority_counts": {
            priority: sum(item["priority"] == priority for item in figure_rows)
            for priority in ("P0", "P1")
        },
        "table_review": str(table_review_path),
        "figure_review": str(figure_review_path),
    }
    (OUTPUT_DIR / "candidate_triage_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def plan_upper_processing() -> dict[str, Any]:
    from .quality_review import load_csv

    metadata_path = OUTPUT_DIR / "metadata_review_required.csv"
    table_path = OUTPUT_DIR / "table_review_required.csv"
    if not metadata_path.exists() or not table_path.exists():
        raise FileNotFoundError("请先运行 summarize-upper 和 triage-upper")

    confirmed_rows = []
    for row in load_csv(table_path):
        if row["priority"] != "P0":
            continue
        confirmed_rows.append(
            {
                **row,
                "validation_result": "真实表格",
                "structure_status": "待结构校正",
                "validation_method": "人工视觉抽样确认同组版式",
                "validation_notes": "跨页、合并单元格、竖排文字或水印会污染自动提取结果，不直接进入正式知识层",
            }
        )
    confirmed_path = OUTPUT_DIR / "table_p0_confirmed.csv"
    confirmed_fields = list(confirmed_rows[0]) if confirmed_rows else []
    _write_csv(confirmed_path, confirmed_rows, confirmed_fields)

    queue = []
    for row in load_csv(metadata_path):
        tables = int(row["table_candidates"])
        figures = int(row["figure_candidate_pages"])
        issues = int(row["quality_issues"])
        pages = int(row["pages"])
        if row["knowledge_ready"] == "是":
            phase = "已完成"
            score = -1
            rationale = "已进入正式知识层"
        elif row["parse_route"] == "ocr":
            phase = "第三批：OCR专项"
            score = 10000 + issues * 10 + pages
            rationale = "扫描件需先完成OCR质量复核，再处理表格与图示"
        elif figures >= 15 or tables >= 25:
            phase = "第二批：复杂表图"
            score = 5000 + tables * 3 + figures * 3 + issues * 5 + pages
            rationale = "表格或图示较多，适合在通用流程稳定后批量处理"
        else:
            phase = "第一批：快速入库"
            score = tables * 4 + figures * 4 + issues * 8 + pages
            rationale = "直接文本解析，复核工作量适中，可优先形成可展示成果"
        queue.append(
            {
                "phase": phase,
                "processing_score": score,
                "file_name": row["file_name"],
                "verified_title": row["verified_title"],
                "document_no": row["document_no"],
                "parse_route": row["parse_route"],
                "pages": row["pages"],
                "table_candidates": row["table_candidates"],
                "figure_candidate_pages": row["figure_candidate_pages"],
                "quality_issues": row["quality_issues"],
                "citation_gate": row["citation_gate"],
                "rationale": rationale,
            }
        )
    phase_order = {"第一批：快速入库": 0, "第二批：复杂表图": 1, "第三批：OCR专项": 2, "已完成": 3}
    queue.sort(key=lambda item: (phase_order[item["phase"]], item["processing_score"], item["file_name"]))
    for index, row in enumerate(queue, start=1):
        row["sequence"] = index
    queue_path = OUTPUT_DIR / "upper_processing_queue.csv"
    queue_fields = ["sequence", *[field for field in queue[0] if field != "sequence"]] if queue else []
    _write_csv(queue_path, queue, queue_fields)

    next_document = next((row for row in queue if row["phase"] == "第一批：快速入库"), None)
    summary = {
        "confirmed_p0_tables": len(confirmed_rows),
        "confirmed_p0_table_review": str(confirmed_path),
        "queue": str(queue_path),
        "phase_counts": {
            phase: sum(row["phase"] == phase for row in queue)
            for phase in phase_order
        },
        "next_document": next_document,
    }
    (OUTPUT_DIR / "upper_processing_plan.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _page_text_without_tables(page: fitz.Page, tables: list[dict[str, Any]]) -> str:
    table_rects = [fitz.Rect(table["bbox"]) for table in tables]
    kept = []
    for block in page.get_text("blocks"):
        rect = fitz.Rect(block[:4])
        overlap = max(
            ((rect & table_rect).get_area() / (rect.get_area() or 1) for table_rect in table_rects),
            default=0,
        )
        if overlap < 0.35:
            text = clean_text(block[4])
            if text:
                kept.append((rect.y0, rect.x0, text))
    kept.sort()
    references = "\n".join(
        f"[表格引用：{table['table_title']}，PDF第{table['page']}页，table_id={table['table_id']}]"
        for table in tables
    )
    return clean_text("\n".join(item[2] for item in kept) + "\n\n" + references)


def promote_ready_upper(file_name: str) -> dict[str, Any]:
    from .quality_review import load_csv

    row = next((item for item in upper_rows() if item["file_name"] == file_name), None)
    if not row:
        raise ValueError(f"未找到北京市上位规范：{file_name}")
    if row["parse_route"] != "direct_text":
        raise ValueError("自动晋级仅支持 direct_text 文档；OCR 文档必须先完成专项复核")
    if not row["verified_title"] or row["status"] != "现行有效":
        raise ValueError("文档尚未通过名称和效力状态门禁")

    quality_rows = []
    for route in ("direct_text", "ocr"):
        path = OUTPUT_DIR / f"quality_review_{route}.csv"
        if path.exists():
            quality_rows.extend(item for item in load_csv(path) if item["file_name"] == file_name)
    if quality_rows:
        raise ValueError(f"文档仍有 {len(quality_rows)} 条解析质量告警，不能自动晋级")

    tables = extract_document_tables(row)
    unverified = [table for table in tables if table["needs_review"]]
    if unverified:
        raise ValueError(f"文档仍有 {len(unverified)} 张表格需要结构校正，不能自动晋级")
    for table in tables:
        cells = table["cells"]
        while cells and len(cells[0]) > 1 and all(not row_cells[-1].strip() for row_cells in cells):
            for row_cells in cells:
                row_cells.pop()
        table["columns"] = len(cells[0]) if cells else 0
        table["markdown"] = markdown_table(cells)

    title_overrides = {
        "525745b5dd361839-p0005-t01": "附表一 群体布置时板式居住建筑的间距系数",
        "525745b5dd361839-p0005-t02": "附表二 多栋塔式居住建筑的间距系数",
        "525745b5dd361839-p0006-t01": "附表三 公共建筑的间距系数",
    }
    for table in tables:
        table["table_title"] = title_overrides.get(table["table_id"], table["table_title"])

    common_metadata = {
        "parent_document": row["verified_title"],
        "document_no": row["document_no"],
        "authority": row["issuing_authority"],
        "jurisdiction": "北京市",
        "authority_level": "municipal",
        "publish_date": normalize_date(row["publish_date"]),
        "effective_date": normalize_date(row["effective_date"]),
        "status": row["status"],
        "source_url": clean_source_url(row["source_url"]),
        "citation_status": "已通过" if row["source_url"] else "官方来源链接待补",
        "source_file": row["source_path"],
        "source_sha256": row["sha256"],
    }
    table_dir = KNOWLEDGE_DIR / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    table_documents = []
    for table in tables:
        metadata = {
            "title": f"{row['verified_title']} - {table['table_title']}",
            "content_type": "table",
            "table_id": table["table_id"],
            "page": table["page"],
            **common_metadata,
        }
        frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
        path = table_dir / f"{table['table_id']}.md"
        path.write_text(
            f"---\n{frontmatter}\n---\n\n# {table['table_title']}\n\n{table['markdown']}\n",
            encoding="utf-8",
        )
        table_documents.append(str(path))

    tables_by_page: dict[int, list[dict[str, Any]]] = {}
    for table in tables:
        tables_by_page.setdefault(table["page"], []).append(table)
    document = fitz.open(row["source_path"])
    page_parts = []
    for index, page in enumerate(document, start=1):
        text = _page_text_without_tables(page, tables_by_page.get(index, []))
        if text:
            page_parts.append(f"## PDF 第{index}页\n\n{text}")
    document.close()

    body_metadata = {
        "title": row["verified_title"],
        "document_type": infer_document_type(row["verified_title"], row["document_no"]),
        "content_type": "body",
        **common_metadata,
    }
    frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in body_metadata.items())
    safe_title = re.sub(r'[<>:"/\\|?*]+', "_", row["verified_title"])
    body_path = KNOWLEDGE_DIR / f"{safe_title}.md"
    body_path.write_text(
        f"---\n{frontmatter}\n---\n\n" + "\n\n".join(page_parts) + "\n",
        encoding="utf-8",
    )
    return {
        "title": row["verified_title"],
        "body_document": str(body_path),
        "table_documents": table_documents,
        "verified_tables": len(tables),
        "citation_status": common_metadata["citation_status"],
        "index_status": "等待补齐官方来源链接后执行 ingest",
    }


def _registry_metadata(row: dict[str, str], quality_status: str) -> dict[str, Any]:
    return {
        "parent_document": row["verified_title"],
        "document_no": row["document_no"],
        "authority": row["issuing_authority"],
        "jurisdiction": "北京市",
        "authority_level": "municipal",
        "publish_date": normalize_date(row["publish_date"]),
        "effective_date": normalize_date(row["effective_date"]),
        "status": row["status"],
        "source_url": clean_source_url(row["source_url"]),
        "citation_status": "已通过" if row["source_url"] else "官方来源链接待补",
        "quality_status": quality_status,
        "source_file": row["source_path"],
        "source_sha256": row["sha256"],
        "parse_route": row["parse_route"],
    }


def _parsed_staging_body(row: dict[str, str]) -> str:
    parsed_dir = OUTPUT_DIR / "parsed"
    for path in parsed_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        if row["sha256"] in text[:4000]:
            marker = "\n---\n"
            end = text.find(marker, 4)
            return text[end + len(marker):].strip() if end >= 0 else text.strip()
    raise FileNotFoundError(f"未找到暂存解析结果：{row['file_name']}")


def _write_upper_body(row: dict[str, str], tables: list[dict[str, Any]], quality_status: str) -> Path:
    if row["parse_route"] == "ocr":
        body = _parsed_staging_body(row)
    else:
        tables_by_page: dict[int, list[dict[str, Any]]] = {}
        for table in tables:
            tables_by_page.setdefault(table["page"], []).append(table)
        document = fitz.open(row["source_path"])
        page_parts = []
        for index, page in enumerate(document, start=1):
            text = _page_text_without_tables(page, tables_by_page.get(index, []))
            if text:
                page_parts.append(f"## PDF 第{index}页\n\n{text}")
        document.close()
        body = "\n\n".join(page_parts)

    metadata = {
        "title": row["verified_title"],
        "document_type": infer_document_type(row["verified_title"], row["document_no"]),
        "content_type": "body",
        **_registry_metadata(row, quality_status),
    }
    frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
    safe_title = re.sub(r'[<>:"/\\|?*]+', "_", row["verified_title"])
    path = KNOWLEDGE_DIR / f"{safe_title}.md"
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
    return path


def _write_clean_upper_tables(
    row: dict[str, str], tables: list[dict[str, Any]], quality_status: str
) -> list[str]:
    table_dir = KNOWLEDGE_DIR / "tables"
    table_dir.mkdir(parents=True, exist_ok=True)
    prefix = row["sha256"][:16]
    for old_path in table_dir.glob(f"{prefix}-*.md"):
        old_path.unlink()
    paths = []
    for table in tables:
        cells = table["cells"]
        while cells and len(cells[0]) > 1 and all(not row_cells[-1].strip() for row_cells in cells):
            for row_cells in cells:
                row_cells.pop()
        table["markdown"] = markdown_table(cells)
        metadata = {
            "title": f"{row['verified_title']} - {table['table_title']}",
            "content_type": "table",
            "table_id": table["table_id"],
            "page": table["page"],
            **_registry_metadata(row, quality_status),
        }
        frontmatter = "\n".join(f"{key}: {_yaml_value(value)}" for key, value in metadata.items())
        path = table_dir / f"{table['table_id']}.md"
        path.write_text(
            f"---\n{frontmatter}\n---\n\n# {table['table_title']}\n\n{table['markdown']}\n",
            encoding="utf-8",
        )
        paths.append(str(path))
    return paths


def publish_all_upper() -> dict[str, Any]:
    from .quality_review import load_csv

    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    quality_rows = []
    table_review_rows = []
    figure_review_rows = []
    for route in ("direct_text", "ocr"):
        quality_path = OUTPUT_DIR / f"quality_review_{route}.csv"
        table_path = OUTPUT_DIR / f"table_candidates_{route}.csv"
        figure_path = OUTPUT_DIR / f"figure_candidates_{route}.csv"
        if quality_path.exists():
            quality_rows.extend(load_csv(quality_path))
        if table_path.exists():
            table_review_rows.extend(load_csv(table_path))
        if figure_path.exists():
            figure_review_rows.extend(load_csv(figure_path))
    quality_by_file: dict[str, list[dict[str, str]]] = {}
    for item in quality_rows:
        quality_by_file.setdefault(item["file_name"], []).append(item)

    reports = []
    excluded_assets = []
    for row in upper_rows():
        issues = quality_by_file.get(row["file_name"], [])
        if row["parse_route"] == "ocr":
            quality_status = "OCR文本已生成，低置信度页面需谨慎引用" if issues else "OCR文本已复核"
            all_tables: list[dict[str, Any]] = []
            clean_tables: list[dict[str, Any]] = []
        else:
            all_tables = extract_document_tables(row)
            clean_tables = [table for table in all_tables if not table["needs_review"]]
            quality_status = "正文可检索，视觉页与复杂表格已隔离" if issues or len(clean_tables) < len(all_tables) else "已通过自动质量门禁"
        body_path = _write_upper_body(row, clean_tables, quality_status)
        table_paths = _write_clean_upper_tables(row, clean_tables, quality_status)

        for table in all_tables:
            if table["needs_review"]:
                excluded_assets.append(
                    {
                        "file_name": row["file_name"],
                        "document_title": row["verified_title"],
                        "asset_type": "table",
                        "page": table["page"],
                        "asset_id": table["table_id"],
                        "reason": "结构复杂或疑似水印污染，未进入检索知识层",
                    }
                )
        for issue in issues:
            excluded_assets.append(
                {
                    "file_name": row["file_name"],
                    "document_title": row["verified_title"],
                    "asset_type": issue["issue_type"],
                    "page": issue["page"],
                    "asset_id": "",
                    "reason": issue["issue"],
                }
            )
        for figure in figure_review_rows:
            if figure["file_name"] != row["file_name"]:
                continue
            excluded_assets.append(
                {
                    "file_name": row["file_name"],
                    "document_title": row["verified_title"],
                    "asset_type": "figure",
                    "page": figure["page"],
                    "asset_id": figure["candidate_id"],
                    "reason": "技术图示页面已提取预览，待视觉摘要复核后再进入检索知识层",
                }
            )
        reports.append(
            {
                "file_name": row["file_name"],
                "title": row["verified_title"],
                "parse_route": row["parse_route"],
                "body_document": str(body_path),
                "body_status": "已生成",
                "clean_tables_published": len(table_paths),
                "complex_tables_excluded": len(all_tables) - len(clean_tables),
                "quality_issues_retained": len(issues),
                "citation_status": "已通过" if row["source_url"] else "官方来源链接待补",
                "quality_status": quality_status,
            }
        )

    report_path = OUTPUT_DIR / "all_upper_publish_report.csv"
    _write_csv(report_path, reports, list(reports[0]) if reports else [])
    excluded_path = OUTPUT_DIR / "excluded_assets_review.csv"
    _write_csv(excluded_path, excluded_assets, list(excluded_assets[0]) if excluded_assets else [])
    summary = {
        "documents_published": len(reports),
        "body_documents": sum(item["body_status"] == "已生成" for item in reports),
        "clean_tables_published": sum(item["clean_tables_published"] for item in reports),
        "complex_tables_excluded": sum(item["complex_tables_excluded"] for item in reports),
        "quality_issues_retained": sum(item["quality_issues_retained"] for item in reports),
        "figure_pages_retained_for_review": len(figure_review_rows),
        "citation_links_missing": sum(item["citation_status"] != "已通过" for item in reports),
        "publish_report": str(report_path),
        "excluded_assets_review": str(excluded_path),
        "knowledge_dir": str(KNOWLEDGE_DIR),
    }
    (OUTPUT_DIR / "all_upper_publish_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def plan_complex_assets() -> dict[str, Any]:
    from .quality_review import load_csv

    source_path = OUTPUT_DIR / "excluded_assets_review.csv"
    if not source_path.exists():
        raise FileNotFoundError("请先运行 publish-all-upper")
    rows = []
    priority_documents = (
        "居住", "公共服务设施", "间距", "日照", "养老", "无障碍", "道路", "用地分类"
    )
    for item in load_csv(source_path):
        asset_type = item["asset_type"]
        important_document = any(term in item["document_title"] for term in priority_documents)
        if asset_type == "table":
            priority = "P0" if important_document else "P1"
            treatment = "PP-StructureV3/表格结构模型提取，再用Qwen-VL对照原图校验"
            acceptance = "行列、合并单元格、表头和数值全部与原图一致；抽样准确率>=98%"
        elif asset_type == "figure":
            priority = "P1" if important_document else "P2"
            treatment = "Qwen3-VL生成图示摘要、图号、关联条款和关键参数，人工复核"
            acceptance = "摘要不引入图中不存在的结论；图号、页码、关联条款准确"
        elif asset_type == "low_ocr_confidence":
            priority = "P0" if important_document else "P1"
            treatment = "300DPI重渲染后RapidOCR复识；低置信段使用Qwen3-VL校对"
            acceptance = "关键条款编号、数值、单位和否定词准确；字符准确率>=98%"
        else:
            priority = "P1" if important_document else "P2"
            treatment = "视觉分类为空白页/封面/图示/正文；正文或图示转入对应处理流程"
            acceptance = "页面类型确认，正文和有效图示不得遗漏"
        rows.append(
            {
                "priority": priority,
                **item,
                "treatment": treatment,
                "acceptance_criteria": acceptance,
                "review_status": "待处理",
                "reviewer": "",
                "review_notes": "",
            }
        )
    rows.sort(key=lambda row: (row["priority"], row["document_title"], int(row["page"] or 0), row["asset_type"]))
    output_path = OUTPUT_DIR / "complex_asset_processing_queue.csv"
    _write_csv(output_path, rows, list(rows[0]) if rows else [])
    summary = {
        "assets": len(rows),
        "priority_counts": {
            priority: sum(row["priority"] == priority for row in rows)
            for priority in ("P0", "P1", "P2")
        },
        "type_counts": {
            asset_type: sum(row["asset_type"] == asset_type for row in rows)
            for asset_type in sorted({row["asset_type"] for row in rows})
        },
        "queue": str(output_path),
    }
    (OUTPUT_DIR / "complex_asset_processing_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary
