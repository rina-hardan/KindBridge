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

# הקובץ schema.sql נמצא באותה תיקייה (db)
schema_path = os.path.join(base_dir, "schema.sql")

print("מריץ את סקריפט ה-SQL המלא ליצירת כל הטבלאות...")

try:
    with open(schema_path, "r", encoding="utf-8") as file:
        sql_script = file.read()

    statements = [s.strip() for s in sql_script.split(";") if s.strip()]

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