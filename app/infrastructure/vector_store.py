"""Chroma HTTP client for volunteer resumes.

Callers outside infrastructure use this module only. The client talks to the
Chroma server (CHROMA_HOST / CHROMA_PORT). It does not open a local directory.
Embeddings come from EMBEDDING_PROVIDER, not from Chroma's default model.
"""

from dataclasses import dataclass
from typing import Protocol

from app.domain.matching import TOP_N, cosine_similarity_from_distance
from app.infrastructure.embeddings import Embedder, embedder_from_env

COLLECTION_NAME = "volunteer_resumes"


class EmbeddingFailed(RuntimeError):
    """The embedding provider failed. Leave the request PENDING_REVIEW."""


class VectorStoreUnavailable(RuntimeError):
    """The Chroma server failed. Leave the request PENDING_REVIEW."""


def resume_document(experience: str, skills_json: str) -> str:
    """The embedded résumé: narrative, a space, then the skills JSON array."""
    return f"{experience} {skills_json}"


@dataclass(frozen=True)
class ResumeHit:
    profile_id: str
    similarity: float
    document: str
    metadata: dict


class ResumeVectorStore(Protocol):
    def upsert_resume(
        self,
        *,
        profile_id: str,
        user_id: str,
        experience: str,
        skills_json: str,
        primary_city: str,
        has_vehicle: bool,
    ) -> None: ...

    def delete_resume(self, profile_id: str) -> None: ...

    def query_resumes(self, text: str, *, top_n: int = TOP_N) -> list[ResumeHit]: ...


class NullResumeIndex:
    """Used in tests so registration does not open a Chroma connection."""

    def upsert_resume(
        self,
        *,
        profile_id: str,
        user_id: str,
        experience: str,
        skills_json: str,
        primary_city: str,
        has_vehicle: bool,
    ) -> None:
        return None

    def delete_resume(self, profile_id: str) -> None:
        return None

    def query_resumes(self, text: str, *, top_n: int = TOP_N) -> list[ResumeHit]:
        return []


class _StoredVectors:
    """Stops Chroma from embedding with its own model. Vectors are passed in."""

    def __call__(self, input):
        raise RuntimeError("embeddings are computed by EMBEDDING_PROVIDER before Chroma is called")

    def embed_query(self, documents):
        return self.__call__(documents)

    def embed_documents(self, documents):
        return self.__call__(documents)

    def name(self) -> str:
        return "kindbridge-precomputed"

    @staticmethod
    def build_from_config(config):
        return _StoredVectors()


class ChromaVolunteerVectorStore:
    """HTTP client. The collection lives on the Chroma server, not in a local directory."""

    def __init__(self, host: str, port: int, embedder: Embedder | None = None) -> None:
        self._host = host
        self._port = port
        self._embedder = embedder

    def upsert_resume(
        self,
        *,
        profile_id: str,
        user_id: str,
        experience: str,
        skills_json: str,
        primary_city: str,
        has_vehicle: bool,
    ) -> None:
        document = resume_document(experience, skills_json)
        vector = self._vectors([document])[0]
        try:
            self._collection().upsert(
                ids=[profile_id],
                documents=[document],
                embeddings=[vector],
                metadatas=[{"user_id": user_id, "primary_city": primary_city, "has_vehicle": has_vehicle}],
            )
        except Exception as exc:
            raise VectorStoreUnavailable("chroma upsert failed") from exc

    def delete_resume(self, profile_id: str) -> None:
        """Drop the résumé vector. Called when a volunteer profile is deactivated."""
        try:
            self._collection().delete(ids=[profile_id])
        except Exception as exc:
            raise VectorStoreUnavailable("chroma delete failed") from exc

    def query_resumes(self, text: str, *, top_n: int = TOP_N) -> list[ResumeHit]:
        if top_n < 1:
            return []
        vector = self._vectors([text])[0]
        try:
            result = self._collection().query(
                query_embeddings=[vector],
                n_results=top_n,
                include=["distances", "documents", "metadatas"],
            )
        except Exception as exc:
            raise VectorStoreUnavailable("chroma query failed") from exc
        return _hits(result)

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        embedder = self._embedder or embedder_from_env()
        self._embedder = embedder
        try:
            vectors = embedder.embed_documents(texts)
        except Exception as exc:
            raise EmbeddingFailed("embedding provider failed") from exc
        if len(vectors) != len(texts):
            raise EmbeddingFailed("embedding provider returned the wrong number of vectors")
        return vectors

    def _collection(self):
        import chromadb

        client = chromadb.HttpClient(host=self._host, port=self._port)
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
            embedding_function=_StoredVectors(),
        )


def _hits(result: dict) -> list[ResumeHit]:
    ids = (result.get("ids") or [[]])[0] or []
    distances = (result.get("distances") or [[]])[0] or []
    documents = (result.get("documents") or [[]])[0] or []
    metadatas = (result.get("metadatas") or [[]])[0] or []
    hits: list[ResumeHit] = []
    for index, profile_id in enumerate(ids):
        distance = distances[index] if index < len(distances) and distances[index] is not None else 1.0
        document = documents[index] if index < len(documents) and documents[index] else ""
        metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
        hits.append(
            ResumeHit(
                profile_id=str(profile_id),
                similarity=cosine_similarity_from_distance(distance),
                document=document,
                metadata=dict(metadata),
            )
        )
    return hits
