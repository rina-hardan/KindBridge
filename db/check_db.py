import os
import sys

from dotenv import load_dotenv
from sqlalchemy import text

# Locate the db directory and load .env from the parent directory
base_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(base_dir, ".."))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.repositories.db import make_engine

env_path = os.path.join(base_dir, "..", ".env")
load_dotenv(dotenv_path=env_path)

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("❌ Error: DATABASE_URL is not defined in the .env file")
    exit(1)

print("Checking database connection on Somee from the db directory...")

try:
    engine = make_engine(db_url)
    with engine.connect() as connection:
        # Fetch the list of existing tables
        result = connection.execute(
            text("SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE'")
        )
        tables = [f"{row[0]}.{row[1]}" for row in result]

        print(f"\n✅ SUCCESS: Connection is healthy! Found {len(tables)} tables in the database:")
        for table in tables:
            print(f"  - {table}")

except Exception as e:
    print("\n❌ Connection failed or an error occurred:")
    print(e)
    