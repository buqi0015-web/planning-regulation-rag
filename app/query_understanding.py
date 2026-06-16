from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


CLAUSE_REFERENCE_RE = re.compile(r"(第[一二三四五六七八九十百千万零〇\d]+条|\d+(?:\.\d+){1,3})")

JURISDICTION_ALIASES = {
    "北京市": ("北京", "北京市", "市级"),
    "密云区": ("密云", "密云区"),
    "国家": ("国家", "全国", "国标", "国家标准"),
}

BUSINESS_ENTITY_ALIASES = {
    "养老服务设施": ("养老", "养老设施", "老年", "老年服务", "养老服务设施"),
    "幼儿园": ("幼儿园", "托幼", "托儿", "学前"),
    "日照": ("日照", "采光", "遮挡", "照度"),
    "建筑退让": ("退线", "退让", "后退", "建筑控制线", "道路红线"),
    "规划许可": ("规划许可", "建设工程规划许可证", "报建", "许可材料", "办理材料"),
    "公共服务设施": ("公共服务设施", "公服", "配套设施", "社区服务"),
    "海绵城市": ("海绵城市", "雨水", "径流", "下凹绿地"),
}

SYNONYM_EXPANSIONS = {
    "退线": ("退让", "后退", "建筑控制线"),
    "退让": ("退线", "后退", "建筑控制线"),
    "养老设施": ("养老服务设施", "老年服务设施"),
    "养老": ("养老服务设施", "老年服务设施"),
    "公服": ("公共服务设施", "配套设施"),
    "配套设施": ("公共服务设施", "公服"),
    "许可证": ("规划许可", "建设工程规划许可证"),
    "材料": ("办理材料", "申请材料"),
    "采光": ("日照", "照度"),
}

TABLE_TERMS = (
    "表",
    "清单",
    "对应",
    "分别",
    "数值",
    "指标",
    "照度值",
    "间距系数",
    "长高比",
    "标准号",
    "实施日期",
)

FIGURE_TERMS = (
    "图",
    "图示",
    "示意图",
    "技术图",
    "附图",
    "基准面",
    "计算起点",
    "扫掠角",
    "建模",
    "复杂形体",
)

APPROVAL_TERMS = ("一定能通过", "一定能够通过", "能不能批", "能否通过审批", "是否合规", "最终容积率")


@dataclass
class QueryUnderstanding:
    original_query: str
    normalized_query: str
    expanded_query: str
    jurisdictions: list[str] = field(default_factory=list)
    business_entities: list[str] = field(default_factory=list)
    content_intent: str = "body"
    clause_refs: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    high_risk_intent: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_query(query: str, max_chars: int = 200) -> str:
    normalized = re.sub(r"\s+", " ", query.replace("\u3000", " ")).strip()
    return normalized[:max_chars]


def _find_aliases(query: str, alias_map: dict[str, tuple[str, ...]]) -> list[str]:
    found = []
    for canonical, aliases in alias_map.items():
        if any(alias in query for alias in aliases):
            found.append(canonical)
    return found


def _find_synonyms(query: str) -> list[str]:
    values: list[str] = []
    for term, expansions in SYNONYM_EXPANSIONS.items():
        if term in query:
            values.extend(expansions)
    return list(dict.fromkeys(values))


def understand_query(query: str) -> QueryUnderstanding:
    normalized = normalize_query(query)
    jurisdictions = _find_aliases(normalized, JURISDICTION_ALIASES)
    business_entities = _find_aliases(normalized, BUSINESS_ENTITY_ALIASES)
    synonyms = _find_synonyms(normalized)
    clause_refs = list(dict.fromkeys(CLAUSE_REFERENCE_RE.findall(normalized)))
    wants_figure = any(term in normalized for term in FIGURE_TERMS)
    wants_table = any(term in normalized for term in TABLE_TERMS)
    content_intent = "figure" if wants_figure else "table" if wants_table else "body"
    high_risk_intent = any(term in normalized for term in APPROVAL_TERMS)
    expansion_parts = [normalized, *jurisdictions, *business_entities, *synonyms, *clause_refs]
    expanded_query = " ".join(dict.fromkeys(part for part in expansion_parts if part))
    return QueryUnderstanding(
        original_query=query,
        normalized_query=normalized,
        expanded_query=expanded_query,
        jurisdictions=jurisdictions,
        business_entities=business_entities,
        content_intent=content_intent,
        clause_refs=clause_refs,
        synonyms=synonyms,
        high_risk_intent=high_risk_intent,
    )
