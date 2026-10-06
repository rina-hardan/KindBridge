"""Projection rebuild: clear read models and replay event_store."""

from datetime import datetime

from sqlalchemy import delete, func, insert, select, text, update

from app.projections.rebuild import rebuild
from app.repositories.tables import event_store, login_attempts, users, volunteer_profiles
from tests.conftest import REQUESTER, VOLUNTEER
from tests.test_auth import register


def test_rebuild_restores_projections_and_leaves_login_attempts(client, engine):
    register(client, VOLUNTEER)
    with engine.begin() as conn:
        before_hash = conn.execute(select(users.c.password_hash)).scalar_one()
        before_events = conn.execute(select(func.count()).select_from(event_store)).scalar_one()
        conn.execute(update(users).values(full_name="Tampered", is_admin=True, password_hash="gone"))
        conn.execute(delete(volunteer_profiles))
        conn.execute(
            insert(login_attempts).values(
                email="yossi@example.com",
                attempted_at=datetime(2026, 10, 6, 12, 0, 0),
                succeeded=False,
            )
        )

    replayed = rebuild(engine)

    assert replayed == before_events == 3
    with engine.connect() as conn:
        user = conn.execute(select(users)).one()
        profile = conn.execute(select(volunteer_profiles)).one()
        attempts = conn.execute(select(func.count()).select_from(login_attempts)).scalar_one()
        events_after = conn.execute(select(func.count()).select_from(event_store)).scalar_one()
    assert events_after == before_events
    assert user.full_name == "Yossi Cohen"
    assert user.email == "yossi@example.com"
    assert user.is_admin is False
    assert user.password_hash == before_hash
    assert profile.primary_city == "Haifa"
    assert profile.is_enabled is True
    assert attempts == 1


def test_rebuild_replays_by_seq_when_the_column_exists(client, engine):
    register(client, REQUESTER)
    seen: list[int] = []

    class Probe:
        def apply(self, events, conn) -> None:
            seen.extend(event.version for event in events)

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE event_store ADD COLUMN seq INTEGER"))
        rowids = list(conn.execute(text("SELECT rowid FROM event_store ORDER BY rowid")).scalars())
        for seq, rowid in enumerate(reversed(rowids), start=1):
            conn.execute(
                text("UPDATE event_store SET seq = :seq WHERE rowid = :rowid"),
                {"seq": seq, "rowid": rowid},
            )

    rebuild(engine, projectors=[Probe()])

    assert seen == [2, 1]
