from __future__ import annotations

import re
import json
import os
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any


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

SPELLING_CORRECTIONS = {
    "日昭": "日照",
    "采关": "采光",
    "退距": "退让",
    "建工证": "建设工程规划许可证",
    "规证": "规划许可证",
    "养老设失": "养老设施",
    "公服设施": "公共服务设施",
    "海棉城市": "海绵城市",
    "蜜云": "密云",
}

TIME_PATTERNS = {
    "current": ("现行", "有效", "现在", "当前", "最新", "目前"),
    "historical": ("历史", "旧版", "废止", "曾经", "当时"),
    "future": ("拟", "征求意见", "即将", "未来"),
}

ELLIPSIS_TERMS = ("这个", "该", "上述", "这条", "这个规范", "该规范", "它")


@dataclass
class QueryUnderstanding:
    original_query: str
    normalized_query: str
    corrected_query: str
    completed_query: str
    rewritten_query: str
    expanded_query: str
    jurisdictions: list[str] = field(default_factory=list)
    business_entities: list[str] = field(default_factory=list)
    content_intent: str = "body"
    clause_refs: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    corrections: dict[str, str] = field(default_factory=dict)
    time_intent: str = ""
    query_variants: list[str] = field(default_factory=list)
    rewrite_method: str = "none"
    needs_context_completion: bool = False
    needs_llm_rewrite: bool = False
    high_risk_intent: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_query(query: str, max_chars: int = 200) -> str:
    normalized = re.sub(r"\s+", " ", query.replace("\u3000", " ")).strip()
    return normalized[:max_chars]


def correct_spelling(query: str) -> tuple[str, dict[str, str]]:
    corrected = query
    changes: dict[str, str] = {}
    for wrong, right in SPELLING_CORRECTIONS.items():
        if wrong in corrected:
            corrected = corrected.replace(wrong, right)
            changes[wrong] = right
    return corrected, changes


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


def _detect_time_intent(query: str) -> str:
    for intent, terms in TIME_PATTERNS.items():
        if any(term in query for term in terms):
            return intent
    date_match = re.search(r"(20\d{2}|19\d{2})\s*年?", query)
    return date_match.group(1) if date_match else ""


def _complete_from_context(query: str, context: dict[str, Any] | None) -> tuple[str, bool]:
    if not context or not any(term in query for term in ELLIPSIS_TERMS):
        return query, False
    previous = str(context.get("previous_query") or "").strip()
    if not previous:
        return query, True
    return f"{previous}。追问：{query}", True


def _rule_rewrite(query: str, business_entities: list[str], synonyms: list[str], clause_refs: list[str]) -> str:
    parts = [query, *business_entities, *synonyms, *clause_refs]
    rewritten = " ".join(dict.fromkeys(part for part in parts if part))
    return rewritten or query


def _llm_rewrite(query: str) -> str | None:
    if os.getenv("QUERY_REWRITE_PROVIDER", "none").lower() not in {"openai_compatible", "llm"}:
        return None
    base_url = os.getenv("QUERY_REWRITE_BASE_URL") or os.getenv("LLM_BASE_URL", "")
    api_key = os.getenv("QUERY_REWRITE_API_KEY") or os.getenv("LLM_API_KEY", "")
    model = os.getenv("QUERY_REWRITE_MODEL") or os.getenv("LLM_MODEL", "")
    if not base_url or not api_key or not model:
        return None
    prompt = (
        "将用户的城市规划法规问题改写成适合检索的短查询。"
        "不要改变数字、单位、否定词、地区和条款号；不要添加用户没有表达的事实。"
        "只输出改写后的查询。\n\n"
        f"用户问题：{query}"
    )
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 120,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("QUERY_REWRITE_TIMEOUT_SECONDS", "8"))) as response:
            data = json.loads(response.read().decode("utf-8"))
        rewritten = str(data["choices"][0]["message"]["content"]).strip()
    except Exception:
        return None
    if not rewritten or len(rewritten) > 180:
        return None
    return rewritten


def _needs_llm_rewrite(query: str, synonyms: list[str], context_needed: bool) -> bool:
    return (
        context_needed
        or len(query) > 60
        or any(term in query for term in ("怎么", "咋", "要不要", "能不能", "哪些情况"))
        and not synonyms
    )


def understand_query(query: str, context: dict[str, Any] | None = None) -> QueryUnderstanding:
    normalized = normalize_query(query)
    corrected, corrections = correct_spelling(normalized)
    completed, context_needed = _complete_from_context(corrected, context)
    jurisdictions = _find_aliases(completed, JURISDICTION_ALIASES)
    business_entities = _find_aliases(completed, BUSINESS_ENTITY_ALIASES)
    synonyms = _find_synonyms(completed)
    clause_refs = list(dict.fromkeys(CLAUSE_REFERENCE_RE.findall(completed)))
    wants_figure = any(term in completed for term in FIGURE_TERMS)
    wants_table = any(term in completed for term in TABLE_TERMS)
    content_intent = "figure" if wants_figure else "table" if wants_table else "body"
    high_risk_intent = any(term in completed for term in APPROVAL_TERMS)
    time_intent = _detect_time_intent(completed)
    rule_rewritten = _rule_rewrite(completed, business_entities, synonyms, clause_refs)
    llm_rewritten = _llm_rewrite(completed)
    rewritten = llm_rewritten or rule_rewritten
    rewrite_method = "llm" if llm_rewritten else "rule" if rewritten != completed else "none"
    expansion_parts = [rewritten, *jurisdictions, *business_entities, *synonyms, *clause_refs, time_intent]
    expanded_query = " ".join(dict.fromkeys(part for part in expansion_parts if part))
    variants = list(dict.fromkeys([normalized, corrected, completed, rewritten, expanded_query]))
    return QueryUnderstanding(
        original_query=query,
        normalized_query=normalized,
        corrected_query=corrected,
        completed_query=completed,
        rewritten_query=rewritten,
        expanded_query=expanded_query,
        jurisdictions=jurisdictions,
        business_entities=business_entities,
        content_intent=content_intent,
        clause_refs=clause_refs,
        synonyms=synonyms,
        corrections=corrections,
        time_intent=time_intent,
        query_variants=variants,
        rewrite_method=rewrite_method,
        needs_context_completion=context_needed,
        needs_llm_rewrite=_needs_llm_rewrite(completed, synonyms, context_needed),
        high_risk_intent=high_risk_intent,
    )
