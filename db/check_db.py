import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# איתור תיקיית ה-db וטעינת ה-.env מהתיקייה הראשית למעלה
base_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(base_dir, "..", ".env")
load_dotenv(dotenv_path=env_path)

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ שגיאה: DATABASE_URL אינו מוגדר בקובץ .env")
    exit(1)

print("בודק את החיבור למסד הנתונים ב-Somee מתוך תיקיית db...")

try:
    engine = create_engine(db_url)
    with engine.connect() as connection:
        # שליפת רשימת הטבלאות הקיימות
        result = connection.execute(
            text("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
        )
        tables = [f"{row[0]}.{row[1]}" for row in result]

        print(f"\n✅ SUCCESS: החיבור תקין לחלוטין! נמצאו {len(tables)} טבלאות במסד:")
        for table in tables:
            print(f"  - {table}")

except Exception as e:
    print("\n❌ החיבור נכשל או אירעה שגיאה:")
    print(e)