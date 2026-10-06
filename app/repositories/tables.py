"""SQLAlchemy Core definitions of the tables the auth module touches.

Production tables are created by db/schema.sql; these definitions must stay column-compatible with it.
`event_store.seq` is omitted because SQL Server fills it through IDENTITY and the app never writes it.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)

metadata = MetaData()

event_store = Table(
    "event_store",
    metadata,
    Column("event_id", Uuid, primary_key=True),
    Column("aggregate_id", Uuid, nullable=False, index=True),
    Column("aggregate_type", String(50), nullable=False),
    Column("event_type", String(80), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("correlation_id", Uuid, nullable=True),
    Column("causation_id", Uuid, nullable=True),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("aggregate_id", "version", name="UQ_event_store_aggregate_version"),
)

users = Table(
    "users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("full_name", String(200), nullable=False),
    Column("phone", String(400), nullable=False),
    Column("is_admin", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
)

volunteer_profiles = Table(
    "volunteer_profiles",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, nullable=False, unique=True),
    Column("is_enabled", Boolean, nullable=False, default=True),
    Column("primary_city", String(100), nullable=False),
    Column("has_vehicle", Boolean, nullable=False, default=False),
    Column("skills_json", Text, nullable=False),
    Column("experience", Text, nullable=False),
    Column("base_frequency", String(30), nullable=False),
    Column("availability_status", String(30), nullable=False, default="AVAILABLE"),
    Column("max_active_tasks", Integer, nullable=False, default=1),
    Column("max_parallel_tasks", Integer, nullable=False, default=2),
    Column("current_active_tasks", Integer, nullable=False, default=0),
    Column("current_parallel_tasks", Integer, nullable=False, default=0),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("email", String(255), nullable=False, index=True),
    Column("attempted_at", DateTime, nullable=False),
    Column("succeeded", Boolean, nullable=False),
)
