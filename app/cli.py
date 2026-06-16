from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api_check import check_dashscope
from .answer_eval import default_answer_eval_path, evaluate_answers
from .core import ROOT, ask, evaluate, ingest_directory, prune_index, retrieve
from .document_inventory import build_inventory
from .pdf_parser import parse_pilots
from .quality_review import prioritize_review
from .table_extractor import extract_pilot_tables
from .knowledge_builder import build_verified_knowledge
from .table_audit import audit_first_document_tables
from .figure_extractor import extract_figures
from .upper_batch import (
    plan_complex_assets,
    plan_upper_processing,
    process_upper_documents,
    promote_ready_upper,
    publish_all_upper,
    summarize_upper_processing,
    triage_upper_candidates,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="城市规划法规 RAG CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ingest_parser = subparsers.add_parser("ingest", help="索引指定目录下的 Markdown/TXT")
    ingest_parser.add_argument("--source", default=str(ROOT / "data" / "source"))
    prune_parser = subparsers.add_parser("prune-index", help="删除不属于指定知识目录的旧索引")
    prune_parser.add_argument("--source", default=str(ROOT / "data" / "knowledge"))
    subparsers.add_parser("check-api", help="检查百炼 Embedding 与生成模型 API")
    inventory_parser = subparsers.add_parser("inventory", help="盘点 PDF 并生成解析路由清单")
    inventory_parser.add_argument("--source", required=True, help="PDF 资料集根目录")
    parse_pilots_parser = subparsers.add_parser("parse-pilots", help="解析三份代表性试点 PDF")
    parse_pilots_parser.add_argument("--max-pages", type=int, default=5)
    subparsers.add_parser("prioritize-review", help="为解析质量告警自动标注复核优先级")
    subparsers.add_parser("extract-tables", help="从建筑日照标准试点中分离表格")
    subparsers.add_parser("build-knowledge", help="应用复核结果并生成正文与表格知识块")
    subparsers.add_parser("audit-tables", help="全量审计建筑日照标准中的所有表格候选")
    subparsers.add_parser("extract-figures", help="提取建筑日照标准中的技术图示候选")
    upper_parser = subparsers.add_parser("process-upper", help="批量处理北京市上位规范到暂存层")
    upper_parser.add_argument("--route", choices=["all", "direct_text", "ocr", "hybrid"], default="all")
    subparsers.add_parser("plan-upper", help="生成上位规范处理队列并确认高优先级表格")
    promote_parser = subparsers.add_parser("promote-upper", help="将通过质量门禁的上位规范生成正式知识文件")
    promote_parser.add_argument("--file", required=True, help="document_registry.csv 中的 file_name")
    subparsers.add_parser("plan-complex-assets", help="生成复杂表格、图示和OCR资产处理队列")
    subparsers.add_parser("publish-all-upper", help="分层发布全部北京市上位规范")
    subparsers.add_parser("summarize-upper", help="汇总北京市上位规范暂存处理结果")
    subparsers.add_parser("triage-upper", help="对上位规范表格和图示候选进行优先级分类")

    search_parser = subparsers.add_parser("search", help="只运行检索")
    search_parser.add_argument("query")
    search_parser.add_argument("--top-k", type=int, default=5)

    ask_parser = subparsers.add_parser("ask", help="检索并生成带引用回答")
    ask_parser.add_argument("query")
    ask_parser.add_argument("--top-k", type=int, default=5)

    eval_parser = subparsers.add_parser("eval", help="运行检索评测")
    eval_parser.add_argument("--dataset", default="data/eval/questions.jsonl")
    eval_parser.add_argument("--top-k", type=int, default=5)
    eval_parser.add_argument("--output", default="", help="可选：将评测结果写入 JSON 文件")
    answer_eval_parser = subparsers.add_parser("eval-answers", help="运行回答质量行为评测")
    answer_eval_parser.add_argument("--dataset", default=str(default_answer_eval_path().relative_to(ROOT)))
    answer_eval_parser.add_argument("--top-k", type=int, default=3)
    answer_eval_parser.add_argument("--output", default="", help="可选：将评测结果写入 JSON 文件")

    args = parser.parse_args()
    if args.command == "ingest":
        result = ingest_directory(Path(args.source))
    elif args.command == "prune-index":
        result = prune_index(Path(args.source))
    elif args.command == "check-api":
        result = check_dashscope()
    elif args.command == "inventory":
        result = build_inventory(Path(args.source))
    elif args.command == "parse-pilots":
        result = parse_pilots(args.max_pages)
    elif args.command == "prioritize-review":
        result = prioritize_review()
    elif args.command == "extract-tables":
        result = extract_pilot_tables()
    elif args.command == "build-knowledge":
        result = build_verified_knowledge()
    elif args.command == "audit-tables":
        result = audit_first_document_tables()
    elif args.command == "extract-figures":
        result = extract_figures()
    elif args.command == "process-upper":
        result = process_upper_documents(args.route)
    elif args.command == "summarize-upper":
        result = summarize_upper_processing()
    elif args.command == "triage-upper":
        result = triage_upper_candidates()
    elif args.command == "plan-upper":
        result = plan_upper_processing()
    elif args.command == "plan-complex-assets":
        result = plan_complex_assets()
    elif args.command == "promote-upper":
        result = promote_ready_upper(args.file)
    elif args.command == "publish-all-upper":
        result = publish_all_upper()
    elif args.command == "search":
        result = retrieve(args.query, args.top_k)
    elif args.command == "ask":
        result = ask(args.query, args.top_k)
    elif args.command == "eval-answers":
        result = evaluate_answers(ROOT / Path(args.dataset), args.top_k)
        if args.output:
            output_path = ROOT / Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        result = evaluate(ROOT / Path(args.dataset), args.top_k)
        if args.output:
            output_path = ROOT / Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
