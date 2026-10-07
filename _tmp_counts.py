from sqlalchemy import text

from app.config import Config
from app.repositories.db import make_engine

engine = make_engine(Config.from_env().database_url)
with engine.connect() as conn:
    tables = conn.execute(
        text(
            "SELECT TABLE_SCHEMA, TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"
        )
    ).all()
    for schema, name in tables:
        count = conn.execute(text(f"SELECT COUNT(*) FROM [{schema}].[{name}]")).scalar()
        print(f"{schema}.{name} {count}")
