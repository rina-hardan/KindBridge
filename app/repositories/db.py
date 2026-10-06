from urllib.parse import quote_plus

from sqlalchemy import Engine, create_engine


def make_engine(database_url: str) -> Engine:
    """Accepts a SQLAlchemy URL or a raw ODBC connection string (as in .env.example)."""
    if "://" not in database_url:
        database_url = "mssql+pyodbc:///?odbc_connect=" + quote_plus(database_url)
    return create_engine(database_url, pool_pre_ping=True)
