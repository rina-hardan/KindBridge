"""Hebrew and English names for the same city or skill.

Matching stays deterministic. A known municipality or skill maps to one key.
Anything else matches only the same folded text, so two Hebrew spellings of an
unknown place still agree with each other.
"""

import re
import unicodedata

_GERSHAYIM = "\"'`׳״"
_SPLIT_SKILLS = re.compile(r"[,،、]")


def fold_text(value: str) -> str:
    """Spacing, punctuation, and niqqud folded. Hebrew letters stay Hebrew."""
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("־", " ").replace("-", " ").replace("/", " ")
    for mark in _GERSHAYIM:
        text = text.replace(mark, "")
    return re.sub(r"\s+", " ", text).strip()


def split_skills(raw: str) -> list[str]:
    """Comma-separated skills, including the Arabic comma used on some Hebrew keyboards."""
    return [part.strip() for part in _SPLIT_SKILLS.split(raw) if part.strip()]


def _alias_table(*groups: tuple[str, ...]) -> dict[str, str]:
    table: dict[str, str] = {}
    for group in groups:
        canonical = fold_text(group[0])
        for name in group:
            table[fold_text(name)] = canonical
    return table


_CITIES = _alias_table(
    ("haifa", "חיפה"),
    ("tel aviv", "tel aviv yafo", "tel aviv jaffa", "תל אביב", "תל אביב יפו", "תא"),
    ("jerusalem", "ירושלים"),
    ("beer sheva", "beersheba", "באר שבע"),
    ("rishon lezion", "rishon le zion", "ראשון לציון"),
    ("petah tikva", "petach tikva", "פתח תקווה", "פתח תקוה"),
    ("ashdod", "אשדוד"),
    ("netanya", "נתניה"),
    ("bnei brak", "בני ברק"),
    ("holon", "חולון"),
    ("ramat gan", "רמת גן"),
    ("ashkelon", "אשקלון"),
    ("rehovot", "רחובות"),
    ("bat yam", "בת ים"),
    ("beit shemesh", "בית שמש"),
    ("kfar saba", "כפר סבא"),
    ("herzliya", "herzliyya", "הרצליה"),
    ("hadera", "חדרה"),
    ("modiin", "modi in", "מודיעין", "מודיעין מכבים רעות"),
    ("nazareth", "נצרת"),
    ("lod", "לוד"),
    ("ramla", "רמלה"),
    ("raanana", "רעננה"),
    ("givatayim", "גבעתיים"),
    ("nahariya", "נהריה"),
    ("akko", "acre", "עכו"),
    ("eilat", "אילת"),
    ("tiberias", "טבריה"),
    ("safed", "tzfat", "צפת"),
    ("kiryat ata", "קריית אתא", "קרית אתא"),
    ("kiryat gat", "קריית גת", "קרית גת"),
    ("kiryat motzkin", "קריית מוצקין", "קרית מוצקין"),
    ("kiryat bialik", "קריית ביאליק", "קרית ביאליק"),
    ("kiryat yam", "קריית ים", "קרית ים"),
    ("kiryat shmona", "קריית שמונה", "קרית שמונה"),
    ("kiryat ono", "קריית אונו", "קרית אונו"),
    ("rosh haayin", "ראש העין"),
    ("yavne", "יבנה"),
    ("or yehuda", "אור יהודה"),
    ("ramat hasharon", "רמת השרון"),
    ("hod hasharon", "הוד השרון"),
    ("ness ziona", "נס ציונה"),
    ("dimona", "דימונה"),
    ("afula", "עפולה"),
    ("karmiel", "כרמיאל"),
    ("yokneam", "יקנעם", "יקנעם עילית"),
    ("zichron yaakov", "זכרון יעקב"),
    ("ariel", "אריאל"),
    ("maale adumim", "מעלה אדומים"),
    ("givat shmuel", "גבעת שמואל"),
    ("yehud", "יהוד", "יהוד מונוסון"),
    ("sderot", "שדרות"),
    ("ofakim", "אופקים"),
    ("netivot", "נתיבות"),
    ("gedera", "גדרה"),
    ("umm al fahm", "אום אל פחם"),
    ("rahat", "רהט"),
    ("sakhnin", "סחנין"),
)

_SKILLS = _alias_table(
    ("first aid", "עזרה ראשונה"),
    ("driving", "drive", "נהיגה", "נהג", "נהגת"),
    ("cooking", "cook", "בישול"),
    ("shopping", "קניות", "קניה"),
    ("companionship", "ליווי"),
    ("tutoring", "teaching", "הוראה", "שיעורים"),
    ("cleaning", "ניקיון"),
    ("childcare", "babysitting", "שמירה על ילדים"),
    ("errands", "שליחויות"),
    ("translation", "תרגום"),
    ("gardening", "גינון"),
)


def city_key(value: str) -> str:
    folded = fold_text(value)
    return _CITIES.get(folded, folded)


def skill_key(value: str) -> str:
    folded = fold_text(value)
    if not folded:
        return ""
    return _SKILLS.get(folded, folded)


def known_skill_keys() -> tuple[str, ...]:
    """Canonical skill keys, in vocabulary order. Used to suggest offers on the tasks page."""
    return tuple(dict.fromkeys(_SKILLS.values()))
