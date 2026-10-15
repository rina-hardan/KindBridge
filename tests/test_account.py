"""Account page: personal details, then requester and volunteer enrollment."""

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import false, func, select, true
from sqlalchemy.dialects import mssql

from app.commands.dtos import BootstrapAdminCommand
from app.repositories.tables import event_store, requester_profiles, users, volunteer_profiles
from tests.conftest import REQUESTER, REQUESTER_PROFILE, VOLUNTEER, VOLUNTEER_PROFILE, csrf_post
from tests.test_auth import bus, enable_volunteer, login, register


def account_update(client, **overrides):
    body = {
        "full_name": "Dana Updated",
        "phone": "050-9998877",
        "city": "Tel Aviv",
        "home_address": "8 Rothschild",
    }
    body.update(overrides)
    return csrf_post(client, "/api/me/account", body)


def save_requester(client, profile=None):
    return csrf_post(client, "/api/me/requester", profile or REQUESTER_PROFILE)


def test_profile_page_requires_login(client):
    response = client.get("/me")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_account_api_requires_login(client):
    assert client.get("/api/me/account").status_code == 401
    assert account_update(client).status_code == 401
    assert save_requester(client).status_code == 401
    assert enable_volunteer(client).status_code == 401


def test_register_opens_profile_with_both_entries(client):
    register(client, REQUESTER)

    page = client.get("/me")
    account = client.get("/api/me/account")

    assert page.status_code == 200
    assert "הפרופיל שלך".encode() in page.data
    assert "הבקשות שלי".encode() in page.data
    assert "התנדבות".encode() in page.data
    assert b"readonly" in page.data
    body = account.get_json()
    assert body["email"] == "dana@example.com"
    assert body["phone"] == REQUESTER["phone"]
    assert body["has_requester_profile"] is False
    assert body["has_volunteer_profile"] is False


def test_update_account_keeps_email_and_encrypts_phone(client, engine):
    register(client, REQUESTER)
    login(client, REQUESTER["email"], REQUESTER["password"])

    response = account_update(client, email="other@example.com")

    assert response.status_code == 400
    assert response.get_json()["fields"]["email"]
    with engine.connect() as conn:
        user = conn.execute(select(users)).one()
        payload = json.loads(
            conn.execute(
                select(event_store.c.payload_json).where(event_store.c.event_type == "UserRegistered")
            ).scalar_one()
        )
    assert user.email == "dana@example.com"
    assert user.full_name == "Dana Levi"
    assert "050-9998877" not in payload

    saved = account_update(client)
    assert saved.status_code == 200
    account = client.get("/api/me/account").get_json()
    assert account["full_name"] == "Dana Updated"
    assert account["phone"] == "050-9998877"
    assert account["city"] == "Tel Aviv"
    assert account["email"] == "dana@example.com"
    with engine.connect() as conn:
        stored_phone = conn.execute(select(users.c.phone)).scalar_one()
        updated = json.loads(
            conn.execute(
                select(event_store.c.payload_json).where(event_store.c.event_type == "UserDetailsUpdated")
            ).scalar_one()
        )
    assert stored_phone != "050-9998877"
    assert "050-9998877" not in updated["phone_encrypted"]
    assert "email" not in updated


def test_requests_area_opens_before_a_profile_or_a_ticket(client, engine):
    register(client, REQUESTER)

    area = client.get("/me/requests")
    assert area.status_code == 200
    assert "בקשות עזרה".encode() in area.data
    account = client.get("/me")
    assert account.status_code == 200
    body = account.data.decode()
    assert "הבקשות שלי" in body
    assert 'href="/me/requests"' in body
    assert "מסך הבקשות נפתח ישר" in body
    assert client.get("/api/me/requests").get_json()["total"] == 0

    form = client.get("/me/requester")
    assert form.status_code == 200
    assert "רישום לבקשת עזרה".encode() in form.data

    response = save_requester(client)

    assert response.status_code == 201
    with engine.connect() as conn:
        profile = conn.execute(select(requester_profiles)).one()
        payload = json.loads(
            conn.execute(
                select(event_store.c.payload_json).where(event_store.c.event_type == "RequesterProfileUpdated")
            ).scalar_one()
        )
    assert profile.default_city == "Haifa"
    assert profile.emergency_contact_phone != REQUESTER_PROFILE["emergency_contact_phone"]
    assert REQUESTER_PROFILE["emergency_contact_phone"] not in payload["emergency_contact_phone_encrypted"]
    assert client.get("/me/requester").headers["Location"].endswith("/me/requests")
    area = client.get("/me/requests")
    assert area.status_code == 200
    assert "בקשות עזרה".encode() in area.data
    page = client.get("/me")
    assert "הבקשות שלי".encode() in page.data
    assert "התנדבות".encode() in page.data


def test_volunteer_area_opens_before_a_profile_exists(client, engine):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])

    tasks = client.get("/me/tasks")
    assert tasks.status_code == 200
    body = tasks.data.decode()
    assert "הוספת התנדבות חדשה" in body
    assert "התנדבויות שלקחת על עצמך" in body
    assert "ההתנדבויות שאתה רוצה לעשות" in body
    assert "עדיין אין כאן כלום" in body
    assert "רישום כמתנדב" not in body
    assert "תחום ההתנדבות" not in body

    form = client.get("/me/volunteer")
    assert form.status_code == 200
    text = form.data.decode()
    assert "הוספת התנדבות" in text
    assert ">שמור</button>" in text
    assert "תחום ההתנדבות" in text
    assert "יש רכב" in text
    assert "באיזו תדירות" in text
    assert "תאריכים שבהם אי אפשר לעזור" in text
    assert "אם לא רוצים לציין תאריכים מראש" in text
    assert "משעה" in text
    assert "עד שעה" in text
    assert "ימים מועדפים" in text
    assert 'class="col-12 d-none" id="volunteer-weekdays"' in text
    assert 'hours.classList.remove("d-none")' in text
    assert "עיר שבה אפשר לעזור" not in text

    response = enable_volunteer(
        client,
        {
            "has_vehicle": True,
            "experience": "עזרה לקשישים, סבלנות, וניסיון קודם בליווי",
            "base_frequency": "WEEKLY",
        },
    )

    assert response.status_code == 201
    with engine.connect() as conn:
        profile = conn.execute(select(volunteer_profiles)).one()
    assert profile.primary_city == "Haifa"
    assert profile.has_vehicle is True or profile.has_vehicle == 1
    assert json.loads(profile.skills_json) == []
    assert profile.experience == "עזרה לקשישים, סבלנות, וניסיון קודם בליווי"
    assert profile.base_frequency == "WEEKLY"
    assert profile.max_active_tasks == 1
    assert profile.max_parallel_tasks == 2
    again = client.get("/me/tasks")
    assert again.status_code == 200
    assert "הוספת התנדבות חדשה".encode() in again.data
    assert "התנדבויות שלקחת על עצמך".encode() in again.data
    assert "ההתנדבויות שאתה רוצה לעשות".encode() in again.data
    assert "עזרה לקשישים".encode() in again.data
    assert "מוכן לעשות".encode() not in again.data
    assert "ההתנדבות שלי".encode() in client.get("/me").data

    blank = client.get("/me/volunteer")
    assert blank.status_code == 200
    blank_text = blank.data.decode()
    assert "הוספת התנדבות" in blank_text
    assert "עזרה לקשישים" in blank_text
    textarea = blank_text.split('id="experience"', 1)[1].split("</textarea>", 1)[0]
    assert "עזרה לקשישים" not in textarea
    assert "פרופיל מתנדב" not in blank_text


def test_volunteering_schedule_follows_frequency(client, engine):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])

    half = enable_volunteer(
        client,
        dict(VOLUNTEER_PROFILE, base_frequency="ON_DEMAND", preferred_time_from="09:00"),
    )
    assert half.status_code == 400
    assert "preferred_time_to" in half.get_json()["fields"]

    on_demand = enable_volunteer(
        client,
        dict(
            VOLUNTEER_PROFILE,
            base_frequency="ON_DEMAND",
            experience="לפי הצורך בבוקר",
            preferred_time_from="09:00",
            preferred_time_to="12:30",
            preferred_weekdays=["SUN"],
        ),
    )
    assert on_demand.status_code == 201
    weekly = enable_volunteer(
        client,
        dict(
            VOLUNTEER_PROFILE,
            base_frequency="WEEKLY",
            experience="פעם בשבוע",
            preferred_time_from="09:00",
            preferred_time_to="12:30",
            preferred_weekdays=["SUN", "WED"],
        ),
    )
    assert weekly.status_code == 201

    with engine.connect() as conn:
        rows = conn.execute(select(volunteer_profiles).order_by(volunteer_profiles.c.experience)).all()
    by_text = {row.experience: row for row in rows}
    demand = by_text["לפי הצורך בבוקר"]
    assert str(demand.preferred_time_from).startswith("09:00")
    assert str(demand.preferred_time_to).startswith("12:30")
    assert demand.preferred_weekdays_json is None
    repeating = by_text["פעם בשבוע"]
    assert str(repeating.preferred_time_from).startswith("09:00")
    assert str(repeating.preferred_time_to).startswith("12:30")
    assert json.loads(repeating.preferred_weekdays_json) == ["SUN", "WED"]

    page = client.get(f"/me/volunteer/{weekly.get_json()['profile_id']}").data.decode()
    assert 'id="from_date"' in page
    assert 'id="experience"' not in page
    assert 'id="volunteer-hours"' not in page
    assert 'id="base_frequency"' not in page


def test_volunteering_form_keeps_city_and_capacity(client, engine):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    enable_volunteer(client, dict(VOLUNTEER_PROFILE, max_active_tasks=3, max_parallel_tasks=0))

    response = csrf_post(
        client,
        "/api/profile",
        {
            "has_vehicle": False,
            "experience": "מלל חופשי חדש",
            "base_frequency": "MONTHLY",
        },
    )

    assert response.status_code == 200
    with engine.connect() as conn:
        profile = conn.execute(select(volunteer_profiles)).one()
    assert profile.primary_city == "Haifa"
    assert json.loads(profile.skills_json) == []
    assert profile.experience == "מלל חופשי חדש"
    assert profile.base_frequency == "MONTHLY"
    assert profile.max_active_tasks == 3
    assert profile.max_parallel_tasks == 0
    assert profile.availability_status == "AVAILABLE"


def test_volunteer_profile_keeps_hebrew_skills(client, engine):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    payload = dict(
        VOLUNTEER_PROFILE,
        primary_city="תל אביב",
        skills=["נהיגה، עזרה ראשונה"],
        experience="מתנדבת במדא",
    )

    response = enable_volunteer(client, payload)

    assert response.status_code == 201
    with engine.connect() as conn:
        profile = conn.execute(select(volunteer_profiles)).one()
    assert profile.primary_city == "תל אביב"
    assert json.loads(profile.skills_json) == ["נהיגה", "עזרה ראשונה"]
    assert profile.experience == "מתנדבת במדא"


def test_hebrew_text_columns_bind_as_nvarchar_on_sql_server():
    import pyodbc
    from sqlalchemy.dialects.mssql.pyodbc import MSDialect_pyodbc

    dialect = MSDialect_pyodbc()
    for column in (
        event_store.c.payload_json,
        users.c.full_name,
        users.c.city,
        users.c.home_address,
        volunteer_profiles.c.primary_city,
        volunteer_profiles.c.skills_json,
        volunteer_profiles.c.experience,
        requester_profiles.c.default_city,
        requester_profiles.c.accessibility_notes,
    ):
        token = column.type.dialect_impl(dialect).get_dbapi_type(pyodbc)
        kind = token[0] if isinstance(token, tuple) else token
        assert kind == pyodbc.SQL_WVARCHAR


def test_volunteer_enrollment_validates_profile(client):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    payload = dict(VOLUNTEER_PROFILE, base_frequency="DAILY", max_active_tasks=0)

    response = enable_volunteer(client, payload)

    fields = response.get_json()["fields"]
    assert response.status_code == 400
    assert "base_frequency" in fields
    assert "max_active_tasks" in fields


def test_second_volunteering_is_a_new_row(client, engine):
    register(client, VOLUNTEER)
    login(client, VOLUNTEER["email"], VOLUNTEER["password"])
    first = enable_volunteer(
        client,
        {
            "has_vehicle": False,
            "experience": "ליווי קשישים ביום ראשון",
            "base_frequency": "WEEKLY",
        },
    )
    assert first.status_code == 201
    first_id = first.get_json()["profile_id"]

    blank = client.get("/me/volunteer")
    blank_text = blank.data.decode()
    textarea = blank_text.split('id="experience"', 1)[1].split("</textarea>", 1)[0]
    assert "ליווי קשישים" not in textarea
    assert "ליווי קשישים ביום ראשון" in blank_text

    second = enable_volunteer(
        client,
        {
            "has_vehicle": True,
            "experience": "הסעת תרופות לבית המרקחת",
            "base_frequency": "ON_DEMAND",
        },
    )
    assert second.status_code == 201
    second_id = second.get_json()["profile_id"]
    assert second_id != first_id

    with engine.connect() as conn:
        rows = conn.execute(select(volunteer_profiles).order_by(volunteer_profiles.c.experience)).all()
    assert [row.experience for row in rows] == ["הסעת תרופות לבית המרקחת", "ליווי קשישים ביום ראשון"]

    page = client.get("/me/tasks").data.decode()
    assert "ליווי קשישים ביום ראשון" in page
    assert "הסעת תרופות לבית המרקחת" in page

    edited = csrf_post(
        client,
        "/api/profile",
        {
            "profile_id": first_id,
            "has_vehicle": False,
            "experience": "ליווי קשישים ביום שני",
            "base_frequency": "WEEKLY",
        },
    )
    assert edited.status_code == 200
    with engine.connect() as conn:
        kept = conn.execute(
            select(volunteer_profiles.c.experience).where(volunteer_profiles.c.id == UUID(second_id))
        ).scalar_one()
    assert kept == "הסעת תרופות לבית המרקחת"
    edit_page = client.get(f"/me/volunteer/{first_id}")
    assert edit_page.status_code == 200
    assert "ליווי קשישים ביום שני" in edit_page.data.decode()
    assert client.get(f"/me/volunteer/{second_id}").status_code == 200


def test_admin_cannot_enroll(app, client):
    bus(app).dispatch(BootstrapAdminCommand.create("root@kindbridge.org", "admin-password-123", "Root"))
    login(client, "root@kindbridge.org", "admin-password-123")

    assert save_requester(client).status_code == 403
    assert enable_volunteer(client).status_code == 403
    assert client.get("/me/requester").headers["Location"].endswith("/me")
    assert "בקשת עזרה".encode() not in client.get("/me").data


def test_login_failure_sql_uses_equality_on_sql_server():
    source = Path("app/repositories/login_attempts.py").read_text(encoding="utf-8")
    assert ".is_(" not in source
    from app.repositories.tables import login_attempts

    statement = select(func.max(login_attempts.c.attempted_at)).where(
        login_attempts.c.email == "a@b.co",
        login_attempts.c.succeeded == true(),
        login_attempts.c.succeeded == false(),
    )
    compiled = str(statement.compile(dialect=mssql.dialect(), compile_kwargs={"literal_binds": True})).upper()
    assert " IS " not in compiled
    assert "= 1" in compiled
    assert "= 0" in compiled
