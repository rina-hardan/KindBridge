"""Embedding providers. Chroma never chooses the model; EMBEDDING_PROVIDER does."""

import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

LOCAL_MODEL = "all-MiniLM-L6-v2"
OPENAI_MODEL = "text-embedding-3-small"
_PLACEHOLDER_KEYS = {"", "your_openai_api_key_here"}


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._api_key = api_key
        self._model = model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        response = OpenAI(api_key=self._api_key).embeddings.create(model=self._model, input=list(texts))
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


class LocalEmbedder:
    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model
        self._model = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]


def embedder_from_env() -> Embedder:
    """OpenAI when a real key is configured; otherwise the local MiniLM fallback."""
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").strip().lower() or "openai"
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    configured_model = os.getenv("EMBEDDING_MODEL", "").strip()
    if provider == "local" or api_key in _PLACEHOLDER_KEYS:
        if provider != "local":
            logger.warning("embedding_provider=local reason=openai_api_key_unconfigured")
        model = LOCAL_MODEL
        if provider == "local" and configured_model and configured_model != OPENAI_MODEL:
            model = configured_model
        logger.info("embedding_provider=local model=%s", model)
        return LocalEmbedder(model)
    model = configured_model or OPENAI_MODEL
    logger.info("embedding_provider=openai model=%s", model)
    return OpenAIEmbedder(api_key, model)
