"""E-mail copy for KindBridge notifications. Pure: no I/O, no database, no sending.

Hebrew is the default (same rule as ``app/i18n.py``); ``lang="en"`` gives the English text.
Mails stay short. They never carry a phone number or an address: a volunteer sees those on
the tasks page after logging in, as the spec requires.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from app.i18n import translate_to


@dataclass(frozen=True)
class RequestInfo:
    request_id: str
    category: str
    city: str
    urgency: str
    preferred_date: date | None = None


@dataclass(frozen=True)
class Candidate:
    name: str
    score: float


@dataclass(frozen=True)
class MailContent:
    subject: str
    body: str


_TEXT = {
    "he": {
        "hello": "שלום {name},",
        "thanks": "תודה על הנכונות לעזור.",
        "category": "קטגוריה",
        "city": "עיר",
        "urgency": "דחיפות",
        "date": "מועד מבוקש",
        "request": "בקשה",
        "link": "קישור",
        "volunteer": "מתנדב/ת",
        "reason": "סיבה",
        "none": "ללא",
        "assigned_volunteer_subject": "שובצת לבקשת עזרה: {category} ב-{city}",
        "assigned_volunteer_intro": "שובצת לבקשת עזרה ב-KindBridge.",
        "assigned_volunteer_details": "הכתובת ופרטי הקשר זמינים לך במסך המשימות לאחר התחברות:",
        "assigned_requester_subject": "מתנדב/ת שובץ/ה לבקשת העזרה שלך",
        "assigned_requester_intro": "מתנדב/ת שובץ/ה לבקשת העזרה שלך.",
        "track": "מעקב אחר הבקשה:",
        "cancelled_subject": "הבקשה בוטלה, השיבוץ שלך שוחרר",
        "cancelled_intro": "בקשת העזרה הבאה בוטלה ואין צורך להגיע. השיבוץ שלך שוחרר.",
        "released_requester_subject": "המתנדב/ת שוחרר/ה מהבקשה שלך",
        "released_requester_intro": "המתנדב/ת ששובץ/ה לבקשה שלך שוחרר/ה ממנה. הבקשה חזרה לתור ואנו מחפשים מתנדב/ת אחר/ת.",
        "released_admin_subject": "משימה שוחררה: {category} ב-{city}",
        "released_admin_intro": "מתנדב/ת שחרר/ה משימה והבקשה חזרה להמתנה לבדיקה.",
        "review": "לטיפול:",
        "proposed_subject": "הצעת התאמה חדשה ממתינה לבדיקה",
        "proposed_intro": "הסוכן הציע מתנדבים לבקשה, וההצעה ממתינה לאישור מנהל.",
        "candidates": "מועמדים מובילים:",
        "score": "ציון",
        "no_match_subject": "לא נמצאה התאמה לבקשה, נדרשת בדיקה",
        "no_match_intro": "הסוכן לא מצא מתנדב מתאים לבקשה.",
        "rejections": "סיכום סיבות דחייה:",
        "rejection.inactive": "לא פעיל",
        "rejection.self_assignment": "הבקשה של המתנדב עצמו",
        "rejection.exemption": "פטור",
        "rejection.declined": "סירב לבקשה הזו",
        "rejection.unavailability": "לא זמין בתאריך",
        "rejection.geography": "עיר לא מתאימה",
        "rejection.vehicle": "חסר רכב",
        "rejection.capacity": "אין קיבולת",
        "rejection.schedule_overlap": "חפיפה בלוח הזמנים",
    },
    "en": {
        "hello": "Hello {name},",
        "thanks": "Thank you for helping.",
        "category": "Category",
        "city": "City",
        "urgency": "Urgency",
        "date": "Preferred date",
        "request": "Request",
        "link": "Link",
        "volunteer": "Volunteer",
        "reason": "Reason",
        "none": "none",
        "assigned_volunteer_subject": "You were assigned to a help request: {category} in {city}",
        "assigned_volunteer_intro": "You were assigned to a help request on KindBridge.",
        "assigned_volunteer_details": "The address and contact details are on your tasks page after you log in:",
        "assigned_requester_subject": "A volunteer was assigned to your help request",
        "assigned_requester_intro": "A volunteer was assigned to your help request.",
        "track": "Follow the request:",
        "cancelled_subject": "The request was cancelled, your assignment is released",
        "cancelled_intro": "The help request below was cancelled. You do not need to go. Your assignment is released.",
        "released_requester_subject": "The volunteer was released from your request",
        "released_requester_intro": (
            "The volunteer assigned to your request was released from it. "
            "The request is back in the queue and we are looking for another volunteer."
        ),
        "released_admin_subject": "Task released: {category} in {city}",
        "released_admin_intro": "A volunteer released a task. The request is waiting for review again.",
        "review": "Review it here:",
        "proposed_subject": "New match proposal waiting for review",
        "proposed_intro": "The agent proposed volunteers for a request. An admin needs to review the proposal.",
        "candidates": "Top candidates:",
        "score": "score",
        "no_match_subject": "No match found for a request, review needed",
        "no_match_intro": "The agent found no suitable volunteer for a request.",
        "rejections": "Rejection summary:",
        "rejection.inactive": "inactive",
        "rejection.self_assignment": "own request",
        "rejection.exemption": "exemption",
        "rejection.declined": "declined this request",
        "rejection.unavailability": "unavailable on the date",
        "rejection.geography": "wrong city",
        "rejection.vehicle": "no vehicle",
        "rejection.capacity": "no capacity",
        "rejection.schedule_overlap": "schedule overlap",
    },
}


def _t(lang: str, key: str, **values: str) -> str:
    table = _TEXT.get(lang) or _TEXT["he"]
    return (table.get(key) or _TEXT["en"].get(key) or key).format(**values)


def _one_line(value: str) -> str:
    """Subjects come from user-entered text. Keep each on a single line."""
    return " ".join(str(value).split())


def _category(lang: str, request: RequestInfo) -> str:
    label = translate_to(lang, f"category.{request.category}")
    return _one_line(label)


def _urgency(lang: str, request: RequestInfo) -> str:
    return _one_line(translate_to(lang, f"urgency.{request.urgency}"))


def _facts(lang: str, request: RequestInfo, *, with_id: bool = False) -> list[str]:
    lines = []
    if with_id:
        lines.append(f"{_t(lang, 'request')}: {request.request_id}")
    lines.append(f"{_t(lang, 'category')}: {_category(lang, request)}")
    lines.append(f"{_t(lang, 'city')}: {_one_line(request.city)}")
    lines.append(f"{_t(lang, 'urgency')}: {_urgency(lang, request)}")
    if request.preferred_date is not None:
        lines.append(f"{_t(lang, 'date')}: {request.preferred_date.isoformat()}")
    return lines


def _subject(lang: str, key: str, request: RequestInfo | None = None) -> str:
    if request is None:
        return _t(lang, key)
    return _one_line(_t(lang, key, category=_category(lang, request), city=_one_line(request.city)))


def assigned_to_volunteer(request: RequestInfo, name: str, link: str, lang: str = "he") -> MailContent:
    body = [
        _t(lang, "hello", name=_one_line(name)),
        _t(lang, "assigned_volunteer_intro"),
        *_facts(lang, request),
        "",
        _t(lang, "assigned_volunteer_details"),
        link,
        "",
        _t(lang, "thanks"),
    ]
    return MailContent(_subject(lang, "assigned_volunteer_subject", request), "\n".join(body))


def assigned_to_requester(
    request: RequestInfo, requester_name: str, volunteer_name: str, link: str, lang: str = "he"
) -> MailContent:
    body = [
        _t(lang, "hello", name=_one_line(requester_name)),
        _t(lang, "assigned_requester_intro"),
        *_facts(lang, request),
        f"{_t(lang, 'volunteer')}: {_one_line(volunteer_name)}",
        "",
        _t(lang, "track"),
        link,
    ]
    return MailContent(_subject(lang, "assigned_requester_subject"), "\n".join(body))


def cancelled_to_volunteer(request: RequestInfo, name: str, lang: str = "he") -> MailContent:
    body = [
        _t(lang, "hello", name=_one_line(name)),
        _t(lang, "cancelled_intro"),
        *_facts(lang, request),
        "",
        _t(lang, "thanks"),
    ]
    return MailContent(_subject(lang, "cancelled_subject"), "\n".join(body))


def released_to_requester(request: RequestInfo, requester_name: str, link: str, lang: str = "he") -> MailContent:
    body = [
        _t(lang, "hello", name=_one_line(requester_name)),
        _t(lang, "released_requester_intro"),
        *_facts(lang, request),
        "",
        _t(lang, "track"),
        link,
    ]
    return MailContent(_subject(lang, "released_requester_subject"), "\n".join(body))


def released_to_admin(
    request: RequestInfo, volunteer_name: str, reason: str | None, link: str, lang: str = "he"
) -> MailContent:
    body = [
        _t(lang, "released_admin_intro"),
        *_facts(lang, request, with_id=True),
        f"{_t(lang, 'volunteer')}: {_one_line(volunteer_name)}",
        f"{_t(lang, 'reason')}: {_one_line(reason or '') or _t(lang, 'none')}",
        "",
        _t(lang, "review"),
        link,
    ]
    return MailContent(_subject(lang, "released_admin_subject", request), "\n".join(body))


def matches_proposed_to_admin(
    request: RequestInfo, candidates: Sequence[Candidate], link: str, lang: str = "he"
) -> MailContent:
    lines = [
        f"{rank}. {_one_line(item.name)} ({_t(lang, 'score')} {item.score:.0f})"
        for rank, item in enumerate(candidates, start=1)
    ]
    body = [
        _t(lang, "proposed_intro"),
        *_facts(lang, request, with_id=True),
        "",
        _t(lang, "candidates"),
        *lines,
        "",
        _t(lang, "review"),
        link,
    ]
    return MailContent(_subject(lang, "proposed_subject"), "\n".join(body))


def no_match_to_admin(
    request: RequestInfo, rejection_summary: Mapping[str, int], link: str, lang: str = "he"
) -> MailContent:
    reasons = [
        f"- {_one_line(_rejection_label(lang, code))}: {count}"
        for code, count in sorted(rejection_summary.items(), key=lambda pair: (-pair[1], pair[0]))
    ] or [f"- {_t(lang, 'none')}"]
    body = [
        _t(lang, "no_match_intro"),
        *_facts(lang, request, with_id=True),
        "",
        _t(lang, "rejections"),
        *reasons,
        "",
        _t(lang, "review"),
        link,
    ]
    return MailContent(_subject(lang, "no_match_subject"), "\n".join(body))


def _rejection_label(lang: str, code: str) -> str:
    key = f"rejection.{code}"
    table = _TEXT.get(lang) or _TEXT["he"]
    return table.get(key) or str(code)
