import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

ALGORITHM = "HS256"
ISSUER = "kindbridge"


@dataclass(frozen=True)
class Identity:
    user_id: uuid.UUID
    roles: tuple[str, ...]

    def has_any(self, *roles: str) -> bool:
        return any(role in self.roles for role in roles)


class InvalidToken(Exception):
    pass


class TokenService:
    def __init__(self, secret: str, ttl_minutes: int):
        self._secret = secret
        self.ttl = timedelta(minutes=ttl_minutes)

    def issue(self, user_id: uuid.UUID, roles: list[str], now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        claims = {
            "sub": str(user_id),
            "roles": list(roles),
            "iss": ISSUER,
            "iat": now,
            "exp": now + self.ttl,
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(claims, self._secret, algorithm=ALGORITHM)

    def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                options={"require": ["sub", "exp", "iat", "iss"]},
            )
            roles = claims.get("roles")
            if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
                raise InvalidToken("roles claim is malformed")
            return Identity(user_id=uuid.UUID(claims["sub"]), roles=tuple(roles))
        except (jwt.PyJWTError, ValueError) as exc:
            raise InvalidToken(str(exc)) from exc
