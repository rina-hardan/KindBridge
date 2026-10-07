"""Screen copy. Hebrew is the default; `kb_lang=en` switches the same screens to English."""

import re

from flask import has_request_context, request

_CATALOG = {
    "en": {
        "nav.logout": "Log out",
        "nav.language": "עברית",
        "login.title": "Log in",
        "login.new": "New to KindBridge?",
        "login.create": "Create an account",
        "register.title": "Create an account",
        "register.lead": "This is just you. Asking for help and volunteering are separate steps on your profile.",
        "register.password_help": "At least 6 characters.",
        "register.submit": "Create account",
        "register.already": "Already registered?",
        "field.email": "Email",
        "field.password": "Password",
        "field.full_name": "Full name",
        "field.phone": "Phone",
        "field.city": "City",
        "field.city_help": "Hebrew or English, for example Haifa or חיפה.",
        "field.home_address": "Home address",
        "profile.title": "Your profile",
        "profile.lead": "These are your personal details. Email stays as it was when you registered.",
        "profile.saved": "Saved.",
        "profile.save": "Save details",
        "profile.choose": "Choose how you take part",
        "profile.ask": "Ask for help",
        "profile.my_requests": "My help requests",
        "profile.ask_ready": "You are registered to ask for help.",
        "profile.ask_needed": "Register the details we keep for someone who needs help.",
        "profile.volunteer": "Volunteer",
        "profile.my_tasks": "My volunteering",
        "profile.volunteer_ready": "You are registered as a volunteer.",
        "profile.volunteer_needed": "Register the details we keep for a volunteer.",
        "requester.title": "Register to ask for help",
        "requester.lead": "These details are saved on your account and can pre-fill later requests.",
        "requester.address": "Address",
        "requester.access": "Accessibility notes",
        "requester.access_help": "Optional. Shown to a volunteer only after they are assigned.",
        "requester.emergency_name": "Emergency contact name",
        "requester.emergency_phone": "Emergency contact phone",
        "requester.save": "Save and continue",
        "requester.back": "Back to profile",
        "volunteer.title": "Register as a volunteer",
        "volunteer.lead": "These details are what KindBridge keeps so you can be matched with requests.",
        "volunteer.city": "City you can help in",
        "volunteer.frequency": "How often can you help?",
        "volunteer.on_demand": "As needed",
        "volunteer.weekly": "Weekly",
        "volunteer.biweekly": "Every two weeks",
        "volunteer.monthly": "Monthly",
        "volunteer.skills": "Skills",
        "volunteer.skills_help": "Separate with commas. Hebrew or English, e.g. first aid or עזרה ראשונה.",
        "volunteer.experience": "Experience",
        "volunteer.vehicle": "I have a vehicle",
        "requests.title": "Help requests",
        "requests.body": "You are registered to ask for help. Your requests will be listed here.",
        "tasks.title": "Volunteering",
        "tasks.body": "You are registered as a volunteer. Your tasks will be listed here.",
        "js.generic_error": "Something went wrong. Please try again.",
        "js.network_error": "Network error. Check your connection and try again.",
    },
    "he": {
        "nav.logout": "יציאה",
        "nav.language": "English",
        "login.title": "כניסה",
        "login.new": "חדשים ב-KindBridge?",
        "login.create": "יצירת חשבון",
        "register.title": "יצירת חשבון",
        "register.lead": "זה רק החשבון האישי. בקשת עזרה והתנדבות נרשמות אחר כך מהפרופיל.",
        "register.password_help": "לפחות 6 תווים.",
        "register.submit": "יצירת חשבון",
        "register.already": "כבר יש חשבון?",
        "field.email": "דוא״ל",
        "field.password": "סיסמה",
        "field.full_name": "שם מלא",
        "field.phone": "טלפון",
        "field.city": "עיר",
        "field.city_help": "אפשר בעברית או באנגלית, למשל חיפה או Haifa.",
        "field.home_address": "כתובת מגורים",
        "profile.title": "הפרופיל שלך",
        "profile.lead": "אלה הפרטים האישיים. כתובת הדוא״ל נקבעת בהרשמה ואינה ניתנת לשינוי.",
        "profile.saved": "הפרטים נשמרו.",
        "profile.save": "שמירת פרטים",
        "profile.choose": "איך משתתפים",
        "profile.ask": "בקשת עזרה",
        "profile.my_requests": "הבקשות שלי",
        "profile.ask_ready": "החשבון רשום לבקש עזרה.",
        "profile.ask_needed": "רישום הפרטים של מי שמבקש עזרה.",
        "profile.volunteer": "התנדבות",
        "profile.my_tasks": "ההתנדבות שלי",
        "profile.volunteer_ready": "החשבון רשום כמתנדב.",
        "profile.volunteer_needed": "רישום הפרטים של מתנדב.",
        "requester.title": "רישום לבקשת עזרה",
        "requester.lead": "הפרטים נשמרים בחשבון ויכולים למלא בקשות בהמשך.",
        "requester.address": "כתובת",
        "requester.access": "הערות נגישות",
        "requester.access_help": "רשות. מוצג למתנדב רק אחרי השיבוץ.",
        "requester.emergency_name": "שם איש קשר לחירום",
        "requester.emergency_phone": "טלפון איש קשר לחירום",
        "requester.save": "שמירה והמשך",
        "requester.back": "חזרה לפרופיל",
        "volunteer.title": "רישום כמתנדב",
        "volunteer.lead": "הפרטים האלה נשמרים כדי להתאים בין מתנדבים לבקשות.",
        "volunteer.city": "עיר שבה אפשר לעזור",
        "volunteer.frequency": "באיזו תדירות אפשר לעזור?",
        "volunteer.on_demand": "לפי הצורך",
        "volunteer.weekly": "פעם בשבוע",
        "volunteer.biweekly": "פעם בשבועיים",
        "volunteer.monthly": "פעם בחודש",
        "volunteer.skills": "כישורים",
        "volunteer.skills_help": "להפריד בפסיקים. עברית או אנגלית, למשל עזרה ראשונה או first aid.",
        "volunteer.experience": "ניסיון",
        "volunteer.vehicle": "יש רכב",
        "requests.title": "בקשות עזרה",
        "requests.body": "החשבון רשום לבקש עזרה. הבקשות יופיעו כאן.",
        "tasks.title": "התנדבות",
        "tasks.body": "החשבון רשום כמתנדב. המשימות יופיעו כאן.",
        "js.generic_error": "משהו השתבש. נסו שוב.",
        "js.network_error": "תקלת רשת. בדקו את החיבור ונסו שוב.",
    },
}

_EXACT = {
    "Expected a JSON object": "הבקשה חייבת להיות אובייקט JSON",
    "Roles cannot be chosen at registration": "אי אפשר לבחור תפקיד בזמן ההרשמה",
    "Volunteer details are saved from your account page": "פרטי ההתנדבות נשמרים מעמוד החשבון",
    "Email is required": "יש להזין דוא״ל",
    "Enter a valid email address": "יש להזין כתובת דוא״ל תקינה",
    "Password is required": "יש להזין סיסמה",
    "This field is required": "שדה חובה",
    "Must be text": "יש להזין טקסט",
    "Enter a valid phone number": "יש להזין מספר טלפון תקין",
    "Email cannot be changed": "אי אפשר לשנות את כתובת הדוא״ל",
    "Skills must be a list of non-empty strings": "יש להזין רשימת כישורים",
    "At most 30 skills, each up to 50 characters": "עד 30 כישורים, וכל כישור עד 50 תווים",
    "Must be true or false": "הערך חייב להיות כן או לא",
    "Expected an object": "הערך חייב להיות אובייקט",
    "Invalid input": "הקלט אינו תקין",
    "Invalid email or password": "הדוא״ל או הסיסמה שגויים",
    "Too many failed login attempts": "יותר מדי ניסיונות כניסה שנכשלו",
    "This email is already registered": "הדוא״ל הזה כבר רשום",
    "An admin account already exists": "חשבון מנהל כבר קיים",
    "This email already belongs to a non-admin account": "הדוא״ל הזה כבר שייך לחשבון שאינו מנהל",
    "An admin account cannot ask for help": "חשבון מנהל לא יכול לבקש עזרה",
    "An admin account cannot volunteer": "חשבון מנהל לא יכול להירשם כמתנדב",
    "This account is already registered as a volunteer": "החשבון כבר רשום כמתנדב",
    "User not found": "המשתמש לא נמצא",
    "Could not record login, please retry": "לא ניתן לרשום את הכניסה, נסו שוב",
    "Login required": "יש להתחבר",
    "Your role cannot perform this action": "אין הרשאה לפעולה הזו",
    "Missing or invalid CSRF token": "אסימון האבטחה חסר או אינו תקין",
    "Help request is not pending review": "בקשת העזרה אינה ממתינה לבדיקה",
}

_PATTERNS = (
    (re.compile(r"^Password must be at least (\d+) characters$"), "הסיסמה חייבת להכיל לפחות {0} תווים"),
    (re.compile(r"^Password must be at most (\d+) bytes$"), "הסיסמה יכולה להכיל לכל היותר {0} בתים"),
    (re.compile(r"^Must be at most (\d+) characters$"), "לכל היותר {0} תווים"),
    (re.compile(r"^Must be a whole number between (\d+) and (\d+)$"), "יש להזין מספר שלם בין {0} ל-{1}"),
    (re.compile(r"^Must be one of (.+)$"), "יש לבחור אחת מהאפשרויות: {0}"),
)


def current_lang() -> str:
    """Hebrew unless the viewer stored `kb_lang=en`."""
    if not has_request_context():
        return "he"
    chosen = request.cookies.get("kb_lang")
    if chosen in ("he", "en"):
        return chosen
    return "he"


def translate(key: str) -> str:
    lang = current_lang()
    catalog = _CATALOG.get(lang) or _CATALOG["he"]
    return catalog.get(key) or _CATALOG["en"].get(key) or key


def localize(message: str) -> str:
    """Turn a stored English failure into Hebrew when the screen language is Hebrew."""
    if current_lang() != "he":
        return message
    if message in _EXACT:
        return _EXACT[message]
    for pattern, template in _PATTERNS:
        match = pattern.match(message)
        if match:
            return template.format(*match.groups())
    return message
