"""Chroma client for volunteer resumes. Callers outside infrastructure use this module only."""

from typing import Protocol

COLLECTION_NAME = "volunteer_resumes"


def resume_document(experience: str, skills_json: str) -> str:
    """The embedded résumé: narrative, a space, then the skills JSON array."""
    return f"{experience} {skills_json}"


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


class ChromaVolunteerVectorStore:
    """HTTP client. The collection lives on the Chroma server, not in a local directory."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

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
        import chromadb

        client = chromadb.HttpClient(host=self._host, port=self._port)
        collection = client.get_or_create_collection(name=COLLECTION_NAME)
        collection.upsert(
            ids=[profile_id],
            documents=[resume_document(experience, skills_json)],
            metadatas=[{"user_id": user_id, "primary_city": primary_city, "has_vehicle": has_vehicle}],
        )
