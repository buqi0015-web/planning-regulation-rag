from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .config import ROOT


def _post(url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {detail[:500]}") from error


def check_dashscope() -> dict[str, Any]:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base_url = os.getenv(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).rstrip("/")
    if not api_key or api_key == "sk-在这里填入你的百炼Key":
        return {
            "status": "not_configured",
            "message": f"请先在 {ROOT / '.env'} 中填写 DASHSCOPE_API_KEY。",
        }

    embedding = _post(
        f"{base_url}/embeddings",
        api_key,
        {
            "model": os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
            "input": ["北京市城市规划法规知识库 API 连通测试"],
            "dimensions": 1024,
            "encoding_format": "float",
        },
    )
    chat = _post(
        f"{base_url}/chat/completions",
        api_key,
        {
            "model": os.getenv("LLM_MODEL", "qwen-plus"),
            "messages": [{"role": "user", "content": "只回复：连接成功"}],
            "temperature": 0,
            "max_tokens": 20,
        },
    )
    return {
        "status": "ok",
        "base_url": base_url,
        "embedding_model": embedding.get("model", os.getenv("EMBEDDING_MODEL", "text-embedding-v4")),
        "embedding_dimensions": len(embedding["data"][0]["embedding"]),
        "llm_model": chat.get("model", os.getenv("LLM_MODEL", "qwen-plus")),
        "llm_response": chat["choices"][0]["message"]["content"],
    }
