"""Volunteer enablement upserts the résumé into Chroma after the event commits."""

import logging
from dataclasses import replace

from sqlalchemy import func, select

from app import build_services, create_app
from app.infrastructure.vector_store import COLLECTION_NAME, ChromaVolunteerVectorStore, resume_document
from app.repositories.tables import users, volunteer_profiles
from tests.conftest import REQUESTER, VOLUNTEER, csrf_post
from tests.test_auth import register


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


def test_volunteer_registration_upserts_resume(config, engine, clock):
    resumes = RecordingResumes()
    response = register(_client(config, engine, clock, resumes), VOLUNTEER)

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


def test_chroma_outage_keeps_the_registered_volunteer(config, engine, clock, caplog):
    client = _client(config, engine, clock, UnavailableResumes())
    with caplog.at_level(logging.ERROR, logger="app.commands.user_commands"):
        response = register(client, VOLUNTEER)

    assert response.status_code == 201
    assert "vector_store_unavailable" in caplog.text
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(users)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(volunteer_profiles)).scalar_one() == 1


def test_live_services_use_the_chroma_http_client(config, engine, clock):
    services = build_services(replace(config, testing=False), engine, clock)
    assert isinstance(services.resumes, ChromaVolunteerVectorStore)


def test_chroma_client_upserts_the_resume_collection(monkeypatch):
    captured: dict = {}

    class Collection:
        def upsert(self, *, ids, documents, metadatas) -> None:
            captured["ids"] = ids
            captured["documents"] = documents
            captured["metadatas"] = metadatas

    class Client:
        def get_or_create_collection(self, name):
            captured["collection"] = name
            return Collection()

    class FakeChroma:
        @staticmethod
        def HttpClient(host, port):
            captured["endpoint"] = (host, port)
            return Client()

    monkeypatch.setitem(__import__("sys").modules, "chromadb", FakeChroma)
    ChromaVolunteerVectorStore("chroma.internal", 8000).upsert_resume(
        profile_id="profile-1",
        user_id="user-1",
        experience="Medic",
        skills_json='["first aid"]',
        primary_city="Haifa",
        has_vehicle=True,
    )

    assert captured["endpoint"] == ("chroma.internal", 8000)
    assert captured["collection"] == COLLECTION_NAME
    assert captured["ids"] == ["profile-1"]
    assert captured["documents"] == ['Medic ["first aid"]']
    assert captured["metadatas"] == [
        {"user_id": "user-1", "primary_city": "Haifa", "has_vehicle": True}
    ]
