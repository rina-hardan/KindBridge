"""Immutable domain events. The event store persists these; projections are derived from them."""

import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

_PLAINTEXT_PASSWORD_KEYS = frozenset({"password", "plain_password", "plaintext_password"})
_REDACTED_KEYS = _PLAINTEXT_PASSWORD_KEYS | frozenset({"password_hash"})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reject_plaintext_password(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        if key.lower() in _PLAINTEXT_PASSWORD_KEYS:
            raise ValueError("event payload must not contain a plaintext password")
        if isinstance(value, dict):
            _reject_plaintext_password(value)


def redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a payload with password material removed, for logs and debug output."""
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _REDACTED_KEYS:
            redacted[key] = "***"
        elif isinstance(value, dict):
            redacted[key] = redact_payload(value)
        else:
            redacted[key] = value
    return redacted


def _json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def payload_to_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False, separators=(",", ":"))


def payload_from_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("event payload_json must be a JSON object")
    return data


def _coerce_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _coerce_optional_uuid(value: Any) -> UUID | None:
    if value is None or value == "":
        return None
    return _coerce_uuid(value)


@dataclass(frozen=True, repr=False)
class DomainEvent:
    """One fact appended to an aggregate stream.

    ``version`` is the per-aggregate sequence (1-based). ``payload`` is stored as
    ``payload_json`` and must never include a plaintext password.
    """

    aggregate_id: UUID
    aggregate_type: str
    event_type: str
    payload: Mapping[str, Any]
    version: int
    event_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: UUID | None = None
    causation_id: UUID | None = None

    def __post_init__(self) -> None:
        if not self.aggregate_type:
            raise ValueError("aggregate_type is required")
        if not self.event_type:
            raise ValueError("event_type is required")
        if self.version < 1:
            raise ValueError("event version must be >= 1")
        payload = deepcopy(dict(self.payload))
        _reject_plaintext_password(payload)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "created_at", _as_utc(self.created_at))

    def __repr__(self) -> str:
        return (
            f"DomainEvent(event_type={self.event_type!r}, aggregate_id={self.aggregate_id}, "
            f"version={self.version}, payload={redact_payload(self.payload)!r})"
        )

    def to_row(self) -> dict[str, Any]:
        created_at = self.created_at.astimezone(timezone.utc).replace(tzinfo=None)
        return {
            "event_id": str(self.event_id),
            "aggregate_id": str(self.aggregate_id),
            "aggregate_type": self.aggregate_type,
            "event_type": self.event_type,
            "payload_json": payload_to_json(self.payload),
            "version": self.version,
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "causation_id": None if self.causation_id is None else str(self.causation_id),
            "created_at": created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DomainEvent":
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(created_at, datetime):
            created_at = _as_utc(created_at)
        payload = row.get("payload")
        if payload is None:
            payload = payload_from_json(row["payload_json"])
        return cls(
            event_id=_coerce_uuid(row["event_id"]),
            aggregate_id=_coerce_uuid(row["aggregate_id"]),
            aggregate_type=row["aggregate_type"],
            event_type=row["event_type"],
            payload=payload,
            version=int(row["version"]),
            correlation_id=_coerce_optional_uuid(row.get("correlation_id")),
            causation_id=_coerce_optional_uuid(row.get("causation_id")),
            created_at=created_at,
        )
