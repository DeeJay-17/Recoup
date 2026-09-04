"""Provider-agnostic embeddings. LangChain-backed (Gemini, OpenAI, ...) or a deterministic
hashing embedder for key-less runs. Vectors are L2-normalised and sized to ``dim``."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbedSettings(BaseSettings):
    """EMBED_* env vars. Provider defaults follow LLM_PROVIDER when unset."""

    model_config = SettingsConfigDict(env_prefix="EMBED_", env_file=".env", extra="ignore")

    provider: str | None = None  # google_genai | openai | ... | hash
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    dim: int = 768
    batch_size: int = 64


DEFAULT_EMBED_MODELS = {
    "google_genai": "gemini-embedding-001",
    "openai": "text-embedding-3-small",
    "azure_openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
    "mistralai": "mistral-embed",
    "cohere": "embed-english-v3.0",
}


class EmbeddingClient(Protocol):
    provider: str
    model: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


def _normalise(v: list[float], dim: int) -> list[float]:
    v = list(v[:dim]) + [0.0] * max(0, dim - len(v))
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    """Hashing-trick bag of word bigrams. Deterministic, no network, surprisingly serviceable
    for lexical similarity; the FTS side of hybrid search carries the rest."""

    provider = "hash"
    model = "hash-bigram"

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = re.findall(r"[a-z0-9]+", text.lower())
        for i, t in enumerate(toks):
            for feat in (t, f"{t}_{toks[i + 1]}" if i + 1 < len(toks) else None):
                if not feat:
                    continue
                h = int(hashlib.blake2b(feat.encode(), digest_size=8).hexdigest(), 16)
                v[h % self.dim] += 1.0 if (h >> 63) else -1.0
        return _normalise(v, self.dim)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._one(text)


class LangChainEmbedder:
    def __init__(self, settings: EmbedSettings, *, provider: str, model: str) -> None:
        from langchain.embeddings import init_embeddings

        self.provider, self.model, self.dim = provider, model, settings.dim
        self._batch = settings.batch_size
        kwargs: dict[str, Any] = {}
        if settings.api_key:
            kwargs["api_key"] = settings.api_key
        if settings.base_url and provider != "google_genai":
            kwargs["base_url"] = settings.base_url
        if provider in ("openai", "azure_openai") and "text-embedding-3" in model:
            kwargs["dimensions"] = settings.dim
        if provider == "google_genai":
            from langchain_google_genai import GoogleGenerativeAIEmbeddings

            try:
                self._emb: Any = GoogleGenerativeAIEmbeddings(model=model, **kwargs)
            except Exception:  # older/newer kwarg name
                kwargs["google_api_key"] = kwargs.pop("api_key", None)
                self._emb = GoogleGenerativeAIEmbeddings(model=model, **kwargs)
        else:
            self._emb = init_embeddings(model, provider=provider, **kwargs)
        self._extra: dict[str, Any] = {}
        if provider == "google_genai":
            self._extra = {"output_dimensionality": settings.dim}

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch):
            chunk = texts[i : i + self._batch]
            try:
                vecs = await self._emb.aembed_documents(chunk, **self._extra)
            except TypeError:
                vecs = await self._emb.aembed_documents(chunk)
            out.extend(_normalise(v, self.dim) for v in vecs)
        return out

    async def embed_query(self, text: str) -> list[float]:
        try:
            v = await self._emb.aembed_query(text, **self._extra)
        except TypeError:
            v = await self._emb.aembed_query(text)
        return _normalise(v, self.dim)


def build_embedder(
    settings: EmbedSettings | None = None,
    *,
    llm_provider: str | None = None,
    llm_api_key: str | None = None,
) -> EmbeddingClient:
    s = settings or EmbedSettings()
    provider = s.provider or (llm_provider if llm_provider in DEFAULT_EMBED_MODELS else "hash")
    if provider == "hash" or provider == "heuristic":
        return HashEmbedder(s.dim)
    model = s.model or DEFAULT_EMBED_MODELS.get(provider, "")
    if not model:
        raise ValueError(f"no default embedding model for provider '{provider}'; set EMBED_MODEL")
    if not s.api_key and llm_api_key:  # empty env values count as unset
        s = s.model_copy(update={"api_key": llm_api_key})
    return LangChainEmbedder(s, provider=provider, model=model)
