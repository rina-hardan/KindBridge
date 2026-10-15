"""Add fictional requesters and volunteers, then propose matches for the new requests.

Does not touch the admin or any account that already exists. Re-running skips an
email that is already registered, writes that résumé into Chroma again, and checks
the person's open request the same way the agent poll does. ``PENDING_REVIEW``,
``MATCH_PROPOSED``, and ``NO_MATCH`` are searched. A request that already has
proposals stays ``MATCH_PROPOSED``: the same ordered volunteers leave the cards
in place, and a different order replaces them. ``NO_MATCH`` is checked again
the same way; volunteers appear only when the ordered ids change.

    python -m db.seed_demo_people

Chroma must already be running (``chroma run --path ./chroma_data``). The shared
password is printed once at the end. Each person both volunteers and submits one
help request, so a person is never a candidate for their own request.
"""

import json
import sys

from dotenv import load_dotenv
from sqlalchemy import select

from app import build_services
from app.commands.dtos import (
    EnableVolunteerProfileCommand,
    RegisterUserCommand,
    SubmitHelpRequestCommand,
    UpdateRequesterProfileCommand,
    normalize_email,
)
from app.commands.match_commands import ProposeMatchCommand
from app.config import Config, ConfigError
from app.domain.errors import EmailAlreadyRegistered, ValidationError
from app.repositories.tables import help_requests, task_assignments, users, volunteer_profiles
from app.repositories.users import UserRepository

PASSWORD = "demo-password-123"
WHEN = "2026-10-20"

# Same phrases inside a topic, so a weak Hebrew embedding still has something to rank.
SHOPPING = "קניות בסופר, להביא מצרכים הביתה, שליחויות לבית מרקחת וסלים לקשישים"
ERRANDS = "שליחויות, קניות בסופר, הבאת מצרכים הביתה וסידורים לבית מרקחת"
TUTORING = "שיעורים פרטיים במתמטיקה ובאנגלית לתלמידים, מרחוק בזום"
TRANSLATION = "תרגום מסמכים מעברית לאנגלית ומאנגלית לעברית, מרחוק"
CHILDCARE = "שמירה על ילדים אחרי הצהריים, משחקים והשגחה בבית"
HOME_HELP = "עזרה בבית, ניקיון, סידור הבית ועזרה קלה לקשישים"
DRIVING = "הסעות ברכב לבית חולים, לקופת חולים ולסידורים, יש לי רכב"
COMPANY = "ליווי וביקור בית, שיחה, הליכה קצרה וחברה לקשישים"
READING = "הקראת ספרים בקול רם לאנשים עם לקות ראייה, בבית"

PEOPLE = (
    {
        "email": "noa.cohen@kindbridge.demo",
        "full_name": "נועה כהן",
        "phone": "050-1001001",
        "city": "ירושלים",
        "home_address": "רחוב יפו 10, ירושלים",
        "experience": f"אני עוזרת ב{SHOPPING}. אפשר להגיע הביתה ולאסוף רשימה.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"מחפשת מישהו ל{COMPANY}. צריך להגיע אלי הביתה פעם בשבוע.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "yossi.levi@kindbridge.demo",
        "full_name": "יוסי לוי",
        "phone": "050-1001002",
        "city": "ירושלים",
        "home_address": "רחוב עזה 22, ירושלים",
        "experience": f"יש לי זמן ל{SHOPPING}. אני גר בירושלים ומגיע הביתה.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "shopping",
        "description": f"צריך עזרה ב{SHOPPING}. קשה לי לצאת, אז צריך להגיע אלי הביתה עם המצרכים.",
        "urgency": "HIGH",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "michal.avraham@kindbridge.demo",
        "full_name": "מיכל אברהם",
        "phone": "050-1001003",
        "city": "ירושלים",
        "home_address": "רחוב עמק רפאים 5, ירושלים",
        "experience": f"מתנדבת ל{ERRANDS}. מגיעה הביתה ולוקחת רשימת קניות.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "errands",
        "description": f"צריכה {ERRANDS}. בבקשה להגיע הביתה ולאסוף את הרשימה.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "daniel.peretz@kindbridge.demo",
        "full_name": "דניאל פרץ",
        "phone": "050-1001004",
        "city": "ירושלים",
        "home_address": "דרך חברון 40, ירושלים",
        "experience": f"אני עושה {DRIVING}. גם {SHOPPING} כשצריך רכב.",
        "has_vehicle": True,
        "base_frequency": "ON_DEMAND",
        "category": "transport",
        "description": "צריך הסעה ברכב לבית החולים ולהגיע לאסוף אותי מהבית. אין לי רכב.",
        "urgency": "HIGH",
        "frequency": "ON_DEMAND",
        "requires_vehicle": True,
    },
    {
        "email": "rachel.mizrahi@kindbridge.demo",
        "full_name": "רחל מזרחי",
        "phone": "050-1001005",
        "city": "ירושלים",
        "home_address": "רחוב בצלאל 3, ירושלים",
        "experience": f"אני נותנת {TUTORING}. נוח לי לעבוד מהמחשב.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "tutoring",
        "description": f"מחפשת {TUTORING} לילד בכיתה ח. השיעור יכול להיות בזום.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "ori.biton@kindbridge.demo",
        "full_name": "אורי ביטון",
        "phone": "050-1001006",
        "city": "ירושלים",
        "home_address": "רחוב קינג ג'ורג' 18, ירושלים",
        "experience": f"מורה לאנגלית. {TUTORING} כבר כמה שנים.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "shopping",
        "description": f"צריך מישהו ל{SHOPPING}. להגיע הביתה עם מצרכים מהסופר וגם מבית המרקחת.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "shira.golan@kindbridge.demo",
        "full_name": "שירה גולן",
        "phone": "050-1001007",
        "city": "חיפה",
        "home_address": "שדרות הנשיא 12, חיפה",
        "experience": f"גרה בחיפה ועוזרת ב{SHOPPING}. מגיעה הביתה בשכונה.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "shopping",
        "description": f"צריכה {SHOPPING} בחיפה. צריך להגיע אלי הביתה עם המצרכים.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "itai.hadad@kindbridge.demo",
        "full_name": "איתי חדד",
        "phone": "050-1001008",
        "city": "חיפה",
        "home_address": "רחוב הרצל 7, חיפה",
        "experience": f"נהג בחיפה. {DRIVING} בתוך העיר.",
        "has_vehicle": True,
        "base_frequency": "ON_DEMAND",
        "category": "transport",
        "description": "צריך הסעה ברכב לקופת חולים בחיפה. להגיע לאסוף מהבית.",
        "urgency": "EMERGENCY",
        "frequency": "ON_DEMAND",
        "requires_vehicle": True,
    },
    {
        "email": "tamar.nahum@kindbridge.demo",
        "full_name": "תמר נחום",
        "phone": "050-1001009",
        "city": "באר שבע",
        "home_address": "רחוב רגר 2, באר שבע",
        "experience": f"מתרגמת. {TRANSLATION}. עובדת מהבית.",
        "has_vehicle": False,
        "base_frequency": "ON_DEMAND",
        "category": "translation",
        "description": f"צריכה {TRANSLATION} של מכתב רשמי. אפשר מרחוק, בלי להגיע.",
        "urgency": "NORMAL",
        "frequency": "ON_DEMAND",
        "requires_vehicle": False,
    },
    {
        "email": "lior.shemesh@kindbridge.demo",
        "full_name": "ליאור שמש",
        "phone": "050-1001010",
        "city": "תל אביב",
        "home_address": "רחוב דיזנגוף 90, תל אביב",
        "experience": f"עוזר בתרגום. {TRANSLATION} של טפסים ומכתבים.",
        "has_vehicle": False,
        "base_frequency": "ON_DEMAND",
        "category": "companionship",
        "description": f"מחפש {COMPANY} בתל אביב. צריך להגיע הביתה לשיחה וביקור.",
        "urgency": "LOW",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "yael.dahan@kindbridge.demo",
        "full_name": "יעל דהן",
        "phone": "050-1001011",
        "city": "ירושלים",
        "home_address": "רחוב אגריפס 15, ירושלים",
        "experience": f"יש לי ניסיון ב{CHILDCARE}. מגיעה הביתה אחרי הצהריים.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "childcare",
        "description": f"צריכה {CHILDCARE}. להגיע אלי הביתה אחרי שעות הלימודים.",
        "urgency": "HIGH",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "amos.sharon@kindbridge.demo",
        "full_name": "עמוס שרון",
        "phone": "050-1001012",
        "city": "ירושלים",
        "home_address": "רחוב הנביאים 8, ירושלים",
        "experience": f"עוזר ב{HOME_HELP}. מגיע הביתה ומסדר.",
        "has_vehicle": False,
        "base_frequency": "BIWEEKLY",
        "category": "home_help",
        "description": f"צריך {HOME_HELP}. להגיע הביתה ולעזור בסידור ובניקיון.",
        "urgency": "NORMAL",
        "frequency": "BIWEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "hadas.levin@kindbridge.demo",
        "full_name": "הדס לוין",
        "phone": "050-1002001",
        "city": "ירושלים",
        "home_address": "רחוב בלפור 4, ירושלים",
        "experience": f"אני פנויה ל{SHOPPING}. מגיעה הביתה עם הסלים.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "shopping",
        "description": f"צריכה {SHOPPING}. קשה לי לסחוב, צריך להגיע אלי הביתה.",
        "urgency": "HIGH",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "gilad.malka@kindbridge.demo",
        "full_name": "גלעד מלכה",
        "phone": "050-1002002",
        "city": "ירושלים",
        "home_address": "רחוב קרן היסוד 11, ירושלים",
        "experience": f"מתנדב ל{ERRANDS} בירושלים. מגיע הביתה לאסוף רשימה.",
        "has_vehicle": True,
        "base_frequency": "WEEKLY",
        "category": "errands",
        "description": f"צריך {ERRANDS}. להגיע הביתה ולקחת את הרשימה לבית המרקחת.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "maya.azar@kindbridge.demo",
        "full_name": "מאיה אזר",
        "phone": "050-1002003",
        "city": "ירושלים",
        "home_address": "רחוב הנביאים 21, ירושלים",
        "experience": f"מורה. {TUTORING} לתלמידי תיכון.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "tutoring",
        "description": f"מחפשת {TUTORING} לבת שלי. השיעור בזום, מרחוק.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "ronen.biton@kindbridge.demo",
        "full_name": "רונן ביטון",
        "phone": "050-1002004",
        "city": "חיפה",
        "home_address": "רחוב הנשיא 30, חיפה",
        "experience": f"גר בחיפה ועוזר ב{SHOPPING}. מגיע הביתה עם מצרכים.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "shopping",
        "description": f"צריך {SHOPPING} בחיפה. להגיע אלי הביתה עם המצרכים מהסופר.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "tal.sharabi@kindbridge.demo",
        "full_name": "טל שרעבי",
        "phone": "050-1002005",
        "city": "תל אביב",
        "home_address": "רחוב אלנבי 40, תל אביב",
        "experience": f"יש לי רכב בתל אביב. {DRIVING}.",
        "has_vehicle": True,
        "base_frequency": "ON_DEMAND",
        "category": "transport",
        "description": "צריך הסעה ברכב לקופת חולים בתל אביב. להגיע לאסוף מהבית.",
        "urgency": "HIGH",
        "frequency": "ON_DEMAND",
        "requires_vehicle": True,
    },
    {
        "email": "noam.elkayam@kindbridge.demo",
        "full_name": "נועם אלקיים",
        "phone": "050-1002006",
        "city": "באר שבע",
        "home_address": "רחוב הרצל 16, באר שבע",
        "experience": f"מתרגם מסמכים. {TRANSLATION}.",
        "has_vehicle": False,
        "base_frequency": "ON_DEMAND",
        "category": "translation",
        "description": f"צריך {TRANSLATION} של טופס. אפשר מרחוק, בלי להגיע.",
        "urgency": "NORMAL",
        "frequency": "ON_DEMAND",
        "requires_vehicle": False,
    },
    {
        "email": "adva.navon@kindbridge.demo",
        "full_name": "אדוה נבון",
        "phone": "050-1003001",
        "city": "נתניה",
        "home_address": "רחוב הרצל 14, נתניה",
        "experience": "הצעת המתנדבת: אני מבשלת ארוחות חמות בנתניה ומביאה אותן עד הדלת, כולל מרק ועוף.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "home_help",
        "description": "צריכה מישהו שמבשל ארוחה חמה בנתניה ומביא אותה עד הדלת פעם בשבוע.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "eitan.bar@kindbridge.demo",
        "full_name": "איתן בר",
        "phone": "050-1003002",
        "city": "נתניה",
        "home_address": "שדרות בן גוריון 8, נתניה",
        "experience": "הצעת המתנדב: אני מכין תבשילים בבית בנתניה ומגיע עם קופסאות אוכל חמות.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "home_help",
        "description": "צריך ארוחה חמה בנתניה. מישהו שיבשל ויביא הביתה, בלי הסעה.",
        "urgency": "HIGH",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "rut.shalev@kindbridge.demo",
        "full_name": "רות שלו",
        "phone": "050-1003003",
        "city": "נתניה",
        "home_address": "רחוב ויצמן 3, נתניה",
        "experience": "הצעת המתנדבת: יש לי ניסיון בבישול לקשישים בנתניה. אני מגיעה עם ארוחה מוכנה.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "home_help",
        "description": "מחפשת מתנדב שמכין אוכל חם בנתניה ומגיע איתו הביתה פעם בשבוע.",
        "urgency": "NORMAL",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "yaara.kore@kindbridge.demo",
        "full_name": "יערה קורא",
        "phone": "050-1004001",
        "city": "רמת גן",
        "home_address": "רחוב ביאליק 4, רמת גן",
        "experience": "אני מבשלת ארוחות בבית. אני לא מקריאה ספרים, אינני יכולה להקריא, ואיני עוזרת לאנשים עם לקות ראייה.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"צריך מישהו ל{READING}. צריך להגיע אלי הביתה ברמת גן ולהקריא.",
        "urgency": "HIGH",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "aviv.kore@kindbridge.demo",
        "full_name": "אביב קורא",
        "phone": "050-1004002",
        "city": "רמת גן",
        "home_address": "רחוב הרצל 9, רמת גן",
        "experience": f"אני מקריא ספרים. {READING}. מגיע הביתה ברמת גן.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"מחפש {COMPANY} ברמת גן. צריך להגיע אלי הביתה לשיחה.",
        "urgency": "LOW",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "hila.kore@kindbridge.demo",
        "full_name": "הילה קוראת",
        "phone": "050-1004003",
        "city": "רמת גן",
        "home_address": "רחוב ז'בוטינסקי 12, רמת גן",
        "experience": f"מתנדבת להקראה. {READING}. מגיעה הביתה ברמת גן.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"מחפשת {COMPANY} ברמת גן. צריך להגיע אלי הביתה לביקור.",
        "urgency": "LOW",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "tomer.kore@kindbridge.demo",
        "full_name": "תומר קורא",
        "phone": "050-1004004",
        "city": "רמת גן",
        "home_address": "שדרות ירושלים 3, רמת גן",
        "experience": f"יש לי ניסיון ב{READING}. אני מגיע הביתה וקורא בקול.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"מחפש {COMPANY} ברמת גן. צריך להגיע הביתה לשבת יחד.",
        "urgency": "LOW",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
    {
        "email": "sigal.kore@kindbridge.demo",
        "full_name": "סיגל קורא",
        "phone": "050-1004005",
        "city": "רמת גן",
        "home_address": "רחוב ביאליק 20, רמת גן",
        "experience": f"{READING}. אני לא מקריאה ספרים, אינני יכולה להקריא, ואיני מוכנה לבוא להקראה בבית.",
        "has_vehicle": False,
        "base_frequency": "WEEKLY",
        "category": "companionship",
        "description": f"מחפשת {COMPANY} ברמת גן. צריך להגיע אלי הביתה לשיחה קצרה.",
        "urgency": "LOW",
        "frequency": "WEEKLY",
        "requires_vehicle": False,
    },
)


def _require_chroma(config: Config) -> None:
    import chromadb

    client = chromadb.HttpClient(host=config.chroma_host, port=config.chroma_port)
    client.heartbeat()


def _add_person(services, person: dict) -> tuple[str, object | None]:
    email = normalize_email(person["email"])
    users_repo = UserRepository()
    with services.engine.connect() as conn:
        if users_repo.email_exists(conn, email):
            return "skipped", None

    registered = services.bus.dispatch(
        RegisterUserCommand.from_payload(
            {
                "email": email,
                "password": PASSWORD,
                "full_name": person["full_name"],
                "phone": person["phone"],
                "city": person["city"],
                "home_address": person["home_address"],
            }
        )
    )
    user_id = registered.user_id
    services.bus.dispatch(
        UpdateRequesterProfileCommand.from_payload(
            user_id,
            {
                "default_city": person["city"],
                "default_address": person["home_address"],
                "accessibility_notes": None,
            },
        )
    )
    services.bus.dispatch(
        EnableVolunteerProfileCommand.from_payload(
            user_id,
            {
                "primary_city": person["city"],
                "experience": person["experience"],
                "has_vehicle": person["has_vehicle"],
                "base_frequency": person["base_frequency"],
                "skills": [],
            },
        )
    )
    submitted = services.bus.dispatch(
        SubmitHelpRequestCommand.from_payload(
            user_id,
            {
                "city": person["city"],
                "address": person["home_address"],
                "category": person["category"],
                "description": person["description"],
                "urgency": person["urgency"],
                "frequency": person["frequency"],
                "preferred_date": WHEN,
                "requires_vehicle": person["requires_vehicle"],
            },
        )
    )
    return "created", submitted.request_id


def _index_existing(services, email: str) -> None:
    """Write the résumé vector again. The first save can commit while Chroma embedding fails."""
    repo = UserRepository()
    with services.engine.connect() as conn:
        account = repo.get_auth_record(conn, email)
        if account is None:
            return
        profile = repo.volunteer_profile(conn, account.user_id)
    if profile is None or not profile.is_enabled:
        return
    services.resumes.upsert_resume(
        profile_id=str(profile.profile_id),
        user_id=str(account.user_id),
        experience=profile.experience,
        skills_json=json.dumps(list(profile.skills), ensure_ascii=False),
        primary_city=profile.primary_city,
        has_vehicle=profile.has_vehicle,
    )


def _demo_request(services, email: str):
    repo = UserRepository()
    with services.engine.connect() as conn:
        account = repo.get_auth_record(conn, email)
        if account is None:
            return None
        return conn.execute(
            select(help_requests.c.id, help_requests.c.status, help_requests.c.match_attempt).where(
                help_requests.c.requester_id == account.user_id
            )
        ).first()


def _propose(services, request_id) -> str:
    with services.engine.connect() as conn:
        row = conn.execute(
            select(help_requests.c.status, help_requests.c.match_attempt).where(help_requests.c.id == request_id)
        ).first()
    if row is None:
        return "missing"
    status = row.status
    attempt = int(row.match_attempt)
    if status not in ("PENDING_REVIEW", "MATCH_PROPOSED", "NO_MATCH"):
        return f"left as {status}"
    result = services.bus.dispatch(ProposeMatchCommand(request_id=request_id, match_attempt=attempt))
    with services.engine.connect() as conn:
        after = conn.execute(select(help_requests.c.status).where(help_requests.c.id == request_id)).first()
    later = after.status if after is not None else "missing"
    return f"{status} -> {later}  {result.outcome} ({result.proposed})"


def _print_matches(services, request_ids: list) -> None:
    if not request_ids:
        return
    stmt = (
        select(
            users.c.full_name,
            volunteer_profiles.c.primary_city,
            task_assignments.c.request_id,
            volunteer_profiles.c.experience,
            task_assignments.c.ai_score,
            task_assignments.c.rank_in_batch,
            task_assignments.c.status,
        )
        .select_from(task_assignments)
        .join(volunteer_profiles, volunteer_profiles.c.id == task_assignments.c.volunteer_id)
        .join(users, users.c.id == volunteer_profiles.c.user_id)
        .where(task_assignments.c.request_id.in_(request_ids))
        .order_by(task_assignments.c.request_id, task_assignments.c.rank_in_batch)
    )
    with services.engine.connect() as conn:
        rows = conn.execute(stmt).all()
    print("\nהצעות שנשמרו:")
    for row in rows:
        score = "" if row.ai_score is None else f"{float(row.ai_score):.0f}"
        print(
            f"  בקשה {row.request_id}  דירוג {row.rank_in_batch}  {row.full_name} ({row.primary_city})  "
            f"ציון {score}  {row.status}"
        )
        print(f"    הצעה: {row.experience}")


def main() -> int:
    load_dotenv()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    try:
        _require_chroma(config)
    except Exception as exc:
        print(f"Chroma is not reachable at {config.chroma_host}:{config.chroma_port}: {exc}", file=sys.stderr)
        print("Start it with: chroma run --path ./chroma_data", file=sys.stderr)
        return 1

    services = build_services(config)
    created = 0
    skipped = 0
    request_ids = []
    for person in PEOPLE:
        email = normalize_email(person["email"])
        try:
            status, request_id = _add_person(services, person)
        except (ValidationError, EmailAlreadyRegistered) as exc:
            print(f"{person['email']}: {exc}", file=sys.stderr)
            return 1
        if status == "skipped":
            skipped += 1
            print(f"קיים, דילוג: {person['full_name']} <{email}>")
            _index_existing(services, email)
            existing = _demo_request(services, email)
            if existing is not None:
                request_ids.append(existing.id)
            continue
        created += 1
        print(f"נוצר: {person['full_name']} <{email}>  בקשה {person['category']} ב{person['city']}")
        if request_id is not None:
            request_ids.append(request_id)

    if request_ids:
        print("\nבודק בקשות פתוחות, גם כאלה שכבר יש להן התאמה. בכרטיס תופיע הצעת המתנדב, לא הסבר הציון.")
        for request_id in request_ids:
            outcome = _propose(services, request_id)
            print(f"  {outcome}")
            print(f"  {config.app_base_url}/requests/{request_id}")
        _print_matches(services, request_ids)

    print(f"\nנוספו {created}, דולגו {skipped}.")
    if created:
        print(f"סיסמה משותפת לחשבונות החדשים: {PASSWORD}")
    print("אף חשבון קיים, כולל המנהל, לא שונה.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
