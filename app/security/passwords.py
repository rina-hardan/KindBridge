import bcrypt

MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_BYTES = 72  # bcrypt rejects longer inputs


class PasswordHasher:
    def __init__(self, rounds: int):
        self._rounds = rounds
        # Verified against when the email is unknown, so response time does not reveal which emails exist.
        self._dummy_hash = bcrypt.hashpw(b"kindbridge-dummy-password", bcrypt.gensalt(rounds))

    def hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(self._rounds)).decode("ascii")

    def verify(self, password: str, password_hash: str | None) -> bool:
        candidate = password.encode("utf-8")
        if len(candidate) > MAX_PASSWORD_BYTES:
            return False
        if password_hash is None:
            bcrypt.checkpw(candidate, self._dummy_hash)
            return False
        return bcrypt.checkpw(candidate, password_hash.encode("ascii"))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return f"Password must be at most {MAX_PASSWORD_BYTES} bytes"
    return None
