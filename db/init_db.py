import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text

# טעינת קובץ ה-.env מהתיקייה העליונה
base_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(base_dir, ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.repositories.db import make_engine

env_path = os.path.join(base_dir, "..", ".env")
load_dotenv(dotenv_path=env_path)

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ שגיאה: DATABASE_URL אינו מוגדר בקובץ .env")
    exit(1)

engine = make_engine(db_url)

# schema.sql יוצר אובייקטים חסרים ואינו מוחק את event_store.
# מחיקה מקומית של כל הטבלאות, כולל ה-event store, היא reset_local.sql.
schema_path = os.path.join(base_dir, "schema.sql")


def split_sql(script: str) -> list[str]:
    """Split on semicolons outside string literals and comments."""
    statements: list[str] = []
    buf: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(script):
        ch = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
            if ch == "'":
                in_string = False
            i += 1
            continue
        if ch == "-" and nxt == "-":
            buf.append(ch)
            buf.append(nxt)
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            buf.append(ch)
            buf.append(nxt)
            in_block_comment = True
            i += 2
            continue
        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


print("מריץ את schema.sql (יצירה בלבד, בלי מחיקת event_store)...")

try:
    with open(schema_path, "r", encoding="utf-8") as file:
        sql_script = file.read()

    statements = split_sql(sql_script)

    with engine.begin() as connection:
        for stmt in statements:
            connection.execute(text(stmt))

        result = connection.execute(
            text("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
        )
        tables = [f"{row[0]}.{row[1]}" for row in result]

        print(f"\n SUCCESS: כל הטבלאות נוצרו בהצלחה! נמצאו {len(tables)} טבלאות:")
        for table in tables:
            print(f"  - {table}")

except Exception as e:
    print("\n❌ שגיאה בהרצת הסקריפט:")
    print(e)