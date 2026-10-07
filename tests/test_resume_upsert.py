"""Volunteer enablement upserts the résumé into Chroma after the event commits."""

import logging
from dataclasses import replace
from pathlib import Path

from sqlalchemy import func, select

from app import build_services, create_app
from app.infrastructure.vector_store import COLLECTION_NAME, ChromaVolunteerVectorStore, resume_document
from app.repositories.tables import users, volunteer_profiles
from tests.conftest import REQUESTER, VOLUNTEER, VOLUNTEER_PROFILE, csrf_post
from tests.test_auth import login, register


class RecordingResumes:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def upsert_resume(self, **kwargs) -> None:
        self.calls.append(kwargs)


class UnavailableResumes:
    def upsert_resume(self, **kwargs) -> None:
        raise ConnectionError("chroma refused")


def _client(config, engine, clock, resumes):
    app = create_app(config=config, engine=engine, clock=clock, resumes=resumes)
    client = app.test_client()
    client.get("/api/auth/csrf")
    return client


def test_enabling_volunteer_upserts_resume(config, engine, clock):
    resumes = RecordingResumes()
    client = _client(config, engine, clock, resumes)
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    response = csrf_post(client, "/api/me/volunteer", VOLUNTEER_PROFILE)

    assert response.status_code == 201
    assert len(resumes.calls) == 1
    call = resumes.calls[0]
    with engine.connect() as conn:
        profile = conn.execute(select(volunteer_profiles)).one()
    assert call["profile_id"] == str(profile.id)
    assert call["user_id"] == str(profile.user_id)
    assert call["primary_city"] == "Haifa"
    assert call["has_vehicle"] is True
    assert call["skills_json"] == profile.skills_json
    assert resume_document(call["experience"], call["skills_json"]) == f"{profile.experience} {profile.skills_json}"


def test_requester_registration_does_not_upsert(config, engine, clock):
    resumes = RecordingResumes()
    response = register(_client(config, engine, clock, resumes), REQUESTER)

    assert response.status_code == 201
    assert resumes.calls == []


def test_chroma_outage_keeps_the_enabled_volunteer(config, engine, clock, caplog):
    client = _client(config, engine, clock, UnavailableResumes())
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    with caplog.at_level(logging.ERROR, logger="app.commands.user_commands"):
        response = csrf_post(client, "/api/me/volunteer", VOLUNTEER_PROFILE)

    assert response.status_code == 201
    assert "vector_store_unavailable" in caplog.text
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(users)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(volunteer_profiles)).scalar_one() == 1


def test_live_services_use_the_chroma_http_client(config, engine, clock):
    services = build_services(replace(config, testing=False), engine, clock)
    assert isinstance(services.resumes, ChromaVolunteerVectorStore)


class _FixedEmbedder:
    def embed_documents(self, texts):
        return [[0.25, 0.5, 0.75] for _text in texts]


def _install_fake_chroma(monkeypatch, captured):
    class Collection:
        def upsert(self, *, ids, documents, metadatas, embeddings) -> None:
            captured["ids"] = ids
            captured["documents"] = documents
            captured["metadatas"] = metadatas
            captured["embeddings"] = embeddings

        def delete(self, *, ids) -> None:
            captured["deleted"] = ids

        def query(self, *, query_embeddings, n_results, include) -> dict:
            captured["n_results"] = n_results
            captured["include"] = include
            captured["query_embeddings"] = query_embeddings
            return {
                "ids": [["profile-1"]],
                "distances": [[0.25]],
                "documents": [["Medic"]],
                "metadatas": [[{"user_id": "user-1", "primary_city": "Haifa", "has_vehicle": True}]],
            }

    class Client:
        def get_or_create_collection(self, name, metadata=None, embedding_function=None):
            captured["collection"] = name
            captured["metadata"] = metadata
            captured["embedding_function"] = embedding_function
            return Collection()

    class FakeChroma:
        @staticmethod
        def HttpClient(host, port):
            captured["endpoint"] = (host, port)
            return Client()

    monkeypatch.setitem(__import__("sys").modules, "chromadb", FakeChroma)


def test_chroma_client_upserts_the_resume_collection(monkeypatch):
    captured: dict = {}
    _install_fake_chroma(monkeypatch, captured)
    ChromaVolunteerVectorStore("chroma.internal", 8000, embedder=_FixedEmbedder()).upsert_resume(
        profile_id="profile-1",
        user_id="user-1",
        experience="Medic",
        skills_json='["first aid"]',
        primary_city="Haifa",
        has_vehicle=True,
    )

    assert captured["endpoint"] == ("chroma.internal", 8000)
    assert captured["collection"] == COLLECTION_NAME
    assert captured["metadata"]["hnsw:space"] == "cosine"
    assert captured["ids"] == ["profile-1"]
    assert captured["documents"] == ['Medic ["first aid"]']
    assert captured["embeddings"] == [[0.25, 0.5, 0.75]]
    assert captured["metadatas"] == [
        {"user_id": "user-1", "primary_city": "Haifa", "has_vehicle": True}
    ]


def test_profile_update_replaces_the_chroma_document(monkeypatch):
    captured: dict = {}
    _install_fake_chroma(monkeypatch, captured)
    store = ChromaVolunteerVectorStore("chroma.internal", 8000, embedder=_FixedEmbedder())
    store.upsert_resume(
        profile_id="profile-1",
        user_id="user-1",
        experience="Old resume",
        skills_json='["driving"]',
        primary_city="Haifa",
        has_vehicle=False,
    )
    store.upsert_resume(
        profile_id="profile-1",
        user_id="user-1",
        experience="Weekly grocery runs",
        skills_json='["driving", "shopping"]',
        primary_city="Haifa",
        has_vehicle=True,
    )

    assert captured["ids"] == ["profile-1"]
    assert captured["documents"] == ['Weekly grocery runs ["driving", "shopping"]']
    assert captured["metadatas"] == [
        {"user_id": "user-1", "primary_city": "Haifa", "has_vehicle": True}
    ]


def test_query_uses_provider_embeddings_and_cosine_similarity(monkeypatch):
    captured: dict = {}
    _install_fake_chroma(monkeypatch, captured)
    hits = ChromaVolunteerVectorStore("chroma.internal", 8000, embedder=_FixedEmbedder()).query_resumes(
        "ride to the clinic transport driving",
        top_n=15,
    )

    assert captured["n_results"] == 15
    assert captured["query_embeddings"] == [[0.25, 0.5, 0.75]]
    assert hits[0].profile_id == "profile-1"
    assert hits[0].similarity == 0.75


def test_delete_resume_removes_the_profile_vector(monkeypatch):
    captured: dict = {}
    _install_fake_chroma(monkeypatch, captured)
    ChromaVolunteerVectorStore("chroma.internal", 8000, embedder=_FixedEmbedder()).delete_resume("profile-1")

    assert captured["deleted"] == ["profile-1"]


def test_vector_store_does_not_open_a_local_chroma_directory():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "infrastructure" / "vector_store.py").read_text(
        encoding="utf-8"
    )
    assert "PersistentClient" not in source
    assert "chroma_db" not in source
    assert "HttpClient" in source
