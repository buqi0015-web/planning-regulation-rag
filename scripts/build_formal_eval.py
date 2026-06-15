from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "rag.db"
OUTPUT_PATH = ROOT / "data" / "eval" / "formal_retrieval_v1.jsonl"


def target(title: str, section: str = "", content_type: str = "body", asset_id: str = "") -> dict[str, str]:
    row = {"title": title, "content_type": content_type}
    if section:
        row["section"] = section
    if asset_id:
        row["asset_id"] = asset_id
    return row


BUSINESS_CASES = [
    ("海绵城市规划目标包括哪些内容？", "海绵城市规划编制与评估标准", "4.2.1"),
    ("城市总体规划中的海绵城市规划应包括哪些内容？", "海绵城市规划编制与评估标准", "3.1.1"),
    ("海绵城市专项规划应包括哪些内容？", "海绵城市规划编制与评估标准", "3.1.3"),
    ("建筑日照计算的数据选用次序如何确定？", "建筑日照计算参数标准", "3.0.4"),
    ("日照计算是否应采用计算机图形文件？", "建筑日照计算参数标准", "3.0.5"),
    ("“平急两用”公共服务设施重点针对哪些设施？", "公共服务设施嵌入“平急两用”功能设计指南（试行）", "1.0.3"),
    ("“三区两通道”具体包括什么？", "公共服务设施嵌入“平急两用”功能设计指南（试行）", "2.0.4"),
    ("“平急两用”设施通过哪些措施转换为应急避难场所？", "公共服务设施嵌入“平急两用”功能设计指南（试行）", "1.0.2"),
    ("公共汽电车场站综合利用指南是否适用于中途站？", "北京市公共汽电车场站综合利用规划设计指南", "1.0.2"),
    ("公共汽电车场站综合利用规划设计应坚持哪些原则？", "北京市公共汽电车场站综合利用规划设计指南", "1.0.3"),
    ("用地同时具备多种用途时应如何归类？", "国土空间调查、规划、用途管制用地分类标准", "4.1.1"),
    ("国土空间详细规划原则上使用哪几级用地分类？", "国土空间调查、规划、用途管制用地分类标准", "4.1.2"),
    ("什么是国土空间规划？", "国土空间调查、规划、用途管制用地分类标准", "3.3"),
    ("什么是国土空间用途管制？", "国土空间调查、规划、用途管制用地分类标准", "3.4"),
    ("建筑日照计算标准适用于哪些建筑和场地？", "建筑日照计算参数标准", "1.0.2"),
    ("海绵城市规划编制与评估标准适用于哪些规划层级？", "海绵城市规划编制与评估标准", "1.0.2"),
    ("社区养老服务设施的公共活动用房应满足哪些基本要求？", "社区养老服务设施设计标准", "6.3.4"),
    ("社区养老设施的活动用房是否需要备用照明？", "社区养老服务设施设计标准", "6.6.3"),
    ("社区养老设施的公共活动用房是否应设置紧急呼叫装置？", "社区养老服务设施设计标准", "6.6.3"),
    ("什么是“平急两用”公共服务设施？", "公共服务设施嵌入“平急两用”功能设计指南（试行）", "2.0.1"),
    ("海绵城市建设设计应遵循什么原则？", "海绵城市建设设计标准", "3.0.1"),
    ("步行和自行车交通环境规划设计的基本原则是什么？", "步行和自行车交通环境规划设计标准", "3.0.1"),
    ("城市道路空间规划设计的基本原则是什么？", "城市道路空间规划设计标准", "3.0.1"),
    ("住宅全装修设计的基本规定是什么？", "住宅全装修设计标准", "3.0.1"),
    ("居住区无障碍设计的基本要求是什么？", "居住区无障碍设计规程", "3.0.1"),
]


TABLE_CASES = [
    ("已建建筑进行日照计算时，数据选用表中的优先顺序是什么？", "28c462afc3ba13b2-p0013-t01"),
    ("在建建筑日照计算优先选用什么图纸？", "28c462afc3ba13b2-p0013-t01"),
    ("已规划建筑日照计算优先选用什么图纸？", "28c462afc3ba13b2-p0013-t01"),
    ("日照计算数据选用表区分了哪些建设阶段？", "28c462afc3ba13b2-p0013-t01"),
    ("社区养老设施公共活动用房的照度值是多少？", "46c63f8fa562f8f7-p0023-t01"),
    ("社区养老设施卫生间的照度值是多少？", "46c63f8fa562f8f7-p0023-t01"),
    ("社区养老设施门厅走廊的照度值范围是多少？", "46c63f8fa562f8f7-p0023-t01"),
    ("社区养老设施生活用房和公共厨房照度值分别是多少？", "46c63f8fa562f8f7-p0023-t01"),
    ("建筑朝向与正南夹角0至20度时，新建区的建筑间距系数是多少？", "525745b5dd361839-p0005-t01"),
    ("建筑朝向与正南夹角20度以上至60度时，改建区间距系数是多少？", "525745b5dd361839-p0005-t01"),
    ("遮挡阳光建筑群长高比2.5以上时，新建区间距系数是多少？", "525745b5dd361839-p0005-t02"),
    ("遮挡阳光建筑群长高比1.0以下时，改建区间距系数是多少？", "525745b5dd361839-p0005-t02"),
    ("建筑朝向与正南夹角60度以上时，建筑间距系数是多少？", "525745b5dd361839-p0006-t01"),
    ("建筑日照计算参数标准的标准号和实施日期是什么？", "28c462afc3ba13b2-p0005-t01"),
    ("批准发布的北京市地方标准目录中，建筑日照计算参数标准是哪一项？", "28c462afc3ba13b2-p0005-t01"),
]


FIGURE_CASES = [
    ("日照计算技术图示中，屋顶突出物是否需要纳入模型？", "28c462afc3ba13b2-p0027-f01"),
    ("复杂建筑形体在日照计算中如何进行合理简化？", "28c462afc3ba13b2-p0027-f02"),
    ("哪张示意图解释了北京大寒日扫掠角变化？", "28c462afc3ba13b2-p0030-f01"),
    ("阳台和窗的日照计算基准面如何选取？", "28c462afc3ba13b2-p0033-f01"),
    ("异型阳台和异型窗的计算基准面如何确定？", "28c462afc3ba13b2-p0033-f02"),
    ("住宅阳台、窗的日照计算起点位于多高？", "28c462afc3ba13b2-p0033-f03"),
]


SCOPE_TITLES = [
    "建筑日照计算参数标准",
    "海绵城市规划编制与评估标准",
    "北京市公共汽电车场站综合利用规划设计指南",
    "公共服务设施嵌入“平急两用”功能设计指南（试行）",
    "绿色建筑设计标准",
    "住宅设计规范",
    "居住建筑节能设计标准",
    "海绵城市建设设计标准",
    "社区养老服务设施设计标准",
    "城市道路空间规划设计标准",
]


MULTI_CASES = [
    (
        "日照计算的数据选用规则和对应表格分别在哪里？",
        [
            target("建筑日照计算参数标准", "3.0.4"),
            target("建筑日照计算参数标准 - 表3.0.4 数据选用表", content_type="table", asset_id="28c462afc3ba13b2-p0013-t01"),
        ],
    ),
    (
        "社区养老设施活动空间照明要求和照度表分别在哪里？",
        [
            target("社区养老服务设施设计标准", "6.6.3"),
            target("社区养老服务设施设计标准 - 表6.6.3 社区养老设施建筑生活、活动及辅助空间照度值", content_type="table", asset_id="46c63f8fa562f8f7-p0023-t01"),
        ],
    ),
    (
        "阳台窗日照计算基准面的文字条款和示意图分别在哪里？",
        [
            target("建筑日照计算参数标准", "5.0.3"),
            target("建筑日照计算参数标准 - 图5.0.3-1", content_type="figure", asset_id="28c462afc3ba13b2-p0033-f01"),
        ],
    ),
    (
        "阳台窗日照计算起点的文字条款和示意图分别在哪里？",
        [
            target("建筑日照计算参数标准", "5.0.4"),
            target("建筑日照计算参数标准 - 图5.0.4", content_type="figure", asset_id="28c462afc3ba13b2-p0033-f03"),
        ],
    ),
]


def clean_body_candidates(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute("SELECT title, section, content, metadata_json FROM chunks").fetchall()
    by_title: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for title, section, content, metadata_json in rows:
        metadata = json.loads(metadata_json)
        compact = " ".join(content.split())
        if metadata.get("content_type", "body") != "body":
            continue
        if not re.fullmatch(r"\d+(?:\.\d+){2,3}", section):
            continue
        if not 80 <= len(compact) <= 900:
            continue
        if any(noise in compact for noise in ("……", "····", "<!--", "PDF 第", "PDF第")):
            continue
        by_title[title].append((title, section, compact))
    selected: list[tuple[str, str, str]] = []
    titles = sorted(by_title)
    index = 0
    while len(selected) < 40 and titles:
        title = titles[index % len(titles)]
        candidates = by_title[title]
        if candidates:
            selected.append(candidates.pop(0))
        else:
            titles.remove(title)
            index -= 1
        index += 1
    if len(selected) != 40:
        raise RuntimeError(f"只能选出 {len(selected)} 道条款定位题")
    return selected


def asset_target(conn: sqlite3.Connection, asset_id: str, content_type: str) -> dict[str, str]:
    rows = conn.execute("SELECT title, section, metadata_json FROM chunks").fetchall()
    for title, section, metadata_json in rows:
        metadata = json.loads(metadata_json)
        current_id = metadata.get("table_id") or metadata.get("figure_id")
        if current_id == asset_id:
            return target(title, section, content_type, asset_id)
    raise RuntimeError(f"未找到正式资产：{asset_id}")


def validate(cases: list[dict], conn: sqlite3.Connection) -> None:
    queries = [case["query"] for case in cases]
    if len(cases) != 100:
        raise RuntimeError(f"正式评测集应为 100 题，当前为 {len(cases)} 题")
    if len(set(queries)) != len(queries):
        raise RuntimeError("评测集中存在重复问题")
    if any("?" * 4 in query for query in queries):
        raise RuntimeError("评测集中存在疑似乱码问题")
    rows = conn.execute("SELECT title, section, metadata_json FROM chunks").fetchall()
    missing = []
    for case in cases:
        for expected in case["relevant"]:
            found = False
            for title, section, metadata_json in rows:
                metadata = json.loads(metadata_json)
                current_id = metadata.get("table_id") or metadata.get("figure_id")
                found = (
                    title == expected["title"]
                    and (not expected.get("section") or expected["section"] in section)
                    and (not expected.get("content_type") or expected["content_type"] == metadata.get("content_type", "body"))
                    and (not expected.get("asset_id") or expected["asset_id"] == current_id)
                )
                if found:
                    break
            if not found:
                missing.append(f"{case['query']} -> {expected}")
    if missing:
        raise RuntimeError("标准证据不存在：\n" + "\n".join(missing))


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cases: list[dict] = []

    for title, section, _ in clean_body_candidates(conn):
        cases.append(
            {
                "id": f"clause-{len(cases) + 1:03d}",
                "query": f"《{title}》第{section}条主要规定了什么？",
                "category": "clause_lookup",
                "difficulty": "easy",
                "relevant": [target(title, section)],
            }
        )

    for query, title, section in BUSINESS_CASES:
        cases.append(
            {
                "id": f"business-{len(cases) + 1:03d}",
                "query": query,
                "category": "business_paraphrase",
                "difficulty": "medium",
                "relevant": [target(title, section)],
            }
        )

    for query, asset_id in TABLE_CASES:
        cases.append(
            {
                "id": f"table-{len(cases) + 1:03d}",
                "query": query,
                "category": "table",
                "difficulty": "medium",
                "relevant": [asset_target(conn, asset_id, "table")],
            }
        )

    for query, asset_id in FIGURE_CASES:
        cases.append(
            {
                "id": f"figure-{len(cases) + 1:03d}",
                "query": query,
                "category": "figure",
                "difficulty": "medium",
                "relevant": [asset_target(conn, asset_id, "figure")],
            }
        )

    for query, relevant in MULTI_CASES:
        cases.append(
            {
                "id": f"multi-{len(cases) + 1:03d}",
                "query": query,
                "category": "multi_evidence",
                "difficulty": "hard",
                "relevant": relevant,
            }
        )

    for title in SCOPE_TITLES:
        cases.append(
            {
                "id": f"scope-{len(cases) + 1:03d}",
                "query": f"《{title}》的适用范围是什么？",
                "category": "scope",
                "difficulty": "medium",
                "relevant": [target(title, "1.0.2")],
            }
        )

    validate(cases, conn)
    conn.close()
    OUTPUT_PATH.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT_PATH), "cases": len(cases)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
