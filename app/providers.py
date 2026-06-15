from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]")


def lexical_terms(text: str) -> list[str]:
    raw = TOKEN_RE.findall(text.lower())
    chinese = "".join(token for token in raw if "\u4e00" <= token <= "\u9fff")
    words = [token for token in raw if not ("\u4e00" <= token <= "\u9fff")]
    bigrams = [chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))]
    return words + bigrams + list(chinese)


def normalize_dense(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class EmbeddingProvider:
    @property
    def signature(self) -> str:
        raise NotImplementedError

    def embed(self, text: str, *, is_query: bool = False) -> dict[str, float] | list[float]:
        raise NotImplementedError

    def embed_many(self, texts: list[str]) -> list[dict[str, float] | list[float]]:
        return [self.embed(text) for text in texts]


class HashEmbeddingProvider(EmbeddingProvider):
    """零成本教学实现，不是真正的语义向量模型。"""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    @property
    def signature(self) -> str:
        return f"hash-v1-{self.dimensions}"

    def embed(self, text: str, *, is_query: bool = False) -> dict[str, float]:
        counts: dict[int, float] = {}
        for term in lexical_terms(text):
            index = int(hashlib.md5(term.encode("utf-8")).hexdigest(), 16) % self.dimensions
            counts[index] = counts.get(index, 0.0) + 1.0
        norm = math.sqrt(sum(value * value for value in counts.values())) or 1.0
        return {str(index): value / norm for index, value in counts.items()}


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """适配支持 `/embeddings` 的 OpenAI 兼容服务。"""

    def __init__(self) -> None:
        self.base_url = os.environ["EMBEDDING_BASE_URL"].rstrip("/")
        self.api_key = os.getenv("EMBEDDING_API_KEY", "")
        self.model = os.environ["EMBEDDING_MODEL"]
        self.query_instruction = os.getenv(
            "EMBEDDING_QUERY_INSTRUCTION",
            "检索与该城市规划法规问题最相关的现行有效条款：",
        )

    @property
    def signature(self) -> str:
        return f"openai-compatible:{self.model}:{self.query_instruction}"

    def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(6):
            request = urllib.request.Request(
                f"{self.base_url}/embeddings",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Connection": "close",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
                ordered = sorted(data["data"], key=lambda item: item.get("index", 0))
                return [normalize_dense(item["embedding"]) for item in ordered]
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Embedding API HTTP {error.code}: {detail}") from error
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                ConnectionResetError,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
            ) as error:
                last_error = error
                if attempt < 5:
                    time.sleep(min(16, 2**attempt))
        raise RuntimeError(f"Embedding API 连续重试失败：{last_error}") from last_error

    def embed(self, text: str, *, is_query: bool = False) -> list[float]:
        input_text = f"{self.query_instruction}{text}" if is_query else text
        return self._request_embeddings([input_text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = max(1, int(os.getenv("EMBEDDING_BATCH_SIZE", "5")))
        for index in range(0, len(texts), batch_size):
            vectors.extend(self._request_embeddings(texts[index : index + batch_size]))
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    provider = os.getenv("EMBEDDING_PROVIDER", "hash").lower()
    if provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider()
    return HashEmbeddingProvider()
