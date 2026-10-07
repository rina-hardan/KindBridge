"""SQLAlchemy Core definitions of the projection tables.

Production tables are created by db/schema.sql; these definitions must stay column-compatible with it.
`event_store.seq` is omitted because SQL Server fills it through IDENTITY and the app never writes it.

User-entered text uses ``Unicode`` / ``UnicodeText`` (NVARCHAR). A ``String`` parameter is sent as
VARCHAR, and SQL Server then replaces Hebrew letters with question marks.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Time,
    Unicode,
    UnicodeText,
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
    Column("payload_json", UnicodeText, nullable=False),
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
    Column("full_name", Unicode(200), nullable=False),
    Column("phone", Unicode(400), nullable=False),
    Column("city", Unicode(100), nullable=True),
    Column("home_address", Unicode(255), nullable=True),
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
    Column("primary_city", Unicode(100), nullable=False),
    Column("has_vehicle", Boolean, nullable=False, default=False),
    Column("skills_json", UnicodeText, nullable=False),
    Column("experience", UnicodeText, nullable=False),
    Column("base_frequency", String(30), nullable=False),
    Column("availability_status", String(30), nullable=False, default="AVAILABLE"),
    Column("max_active_tasks", Integer, nullable=False, default=1),
    Column("max_parallel_tasks", Integer, nullable=False, default=2),
    Column("current_active_tasks", Integer, nullable=False, default=0),
    Column("current_parallel_tasks", Integer, nullable=False, default=0),
)

requester_profiles = Table(
    "requester_profiles",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, nullable=False, unique=True),
    Column("default_city", Unicode(100), nullable=True),
    Column("default_address", Unicode(255), nullable=True),
    Column("accessibility_notes", Unicode(500), nullable=True),
    Column("emergency_contact_name", Unicode(200), nullable=True),
    Column("emergency_contact_phone", Unicode(400), nullable=True),
    Column("updated_at", DateTime, nullable=False),
)

volunteer_unavailability = Table(
    "volunteer_unavailability",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("volunteer_id", Uuid, nullable=False),
    Column("from_date", Date, nullable=False),
    Column("until_date", Date, nullable=False),
    Column("reason", Unicode(200), nullable=True),
    Column("is_cancelled", Boolean, nullable=False, default=False),
    Column("created_at", DateTime, nullable=False),
)

help_requests = Table(
    "help_requests",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("requester_id", Uuid, nullable=False),
    Column("series_id", Uuid, nullable=True),
    Column("city", Unicode(100), nullable=False),
    Column("address", Unicode(255), nullable=False),
    Column("category", String(50), nullable=False),
    Column("resource_type", String(30), nullable=False),
    Column("description", UnicodeText, nullable=False),
    Column("urgency", String(20), nullable=False),
    Column("preferred_date", Date, nullable=True),
    Column("preferred_time_from", Time, nullable=True),
    Column("preferred_time_to", Time, nullable=True),
    Column("estimated_duration_min", Integer, nullable=True),
    Column("required_skills_json", UnicodeText, nullable=False),
    Column("requires_vehicle", Boolean, nullable=False, default=False),
    Column("concurrency_type", String(20), nullable=False, default="UNKNOWN"),
    Column("status", String(30), nullable=False),
    Column("match_attempt", Integer, nullable=False, default=0),
    Column("created_at", DateTime, nullable=False),
)

task_assignments = Table(
    "task_assignments",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("request_id", Uuid, nullable=False),
    Column("volunteer_id", Uuid, nullable=False),
    Column("ai_score", Numeric(5, 2), nullable=True),
    Column("ai_rationale", Unicode(500), nullable=True),
    Column("rank_in_batch", Integer, nullable=True),
    Column("match_attempt", Integer, nullable=False),
    Column("approved_by", Uuid, nullable=True),
    Column("status", String(30), nullable=False),
    Column("decline_reason", Unicode(500), nullable=True),
    Column("override_reason", Unicode(500), nullable=True),
    Column("updated_at", DateTime, nullable=False),
)

exemption_links = Table(
    "exemption_links",
    metadata,
    Column("volunteer_id", Uuid, primary_key=True),
    Column("requester_id", Uuid, primary_key=True),
    Column("created_by", Uuid, nullable=False),
    Column("reason", Unicode(300), nullable=False),
    Column("created_at", DateTime, nullable=False),
)

login_attempts = Table(
    "login_attempts",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("email", String(255), nullable=False, index=True),
    Column("attempted_at", DateTime, nullable=False),
    Column("succeeded", Boolean, nullable=False),
)
