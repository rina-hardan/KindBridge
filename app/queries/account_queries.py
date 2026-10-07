"""Read the signed-in person's account and whether each enrollment exists."""

import uuid
from dataclasses import dataclass

from sqlalchemy import Engine

from app.repositories.users import UserRepository
from app.security.encryption import FieldEncryptor


@dataclass(frozen=True)
class AccountView:
    user_id: uuid.UUID
    email: str
    full_name: str
    phone: str
    city: str | None
    home_address: str | None
    is_admin: bool
    has_requester_profile: bool
    has_volunteer_profile: bool


class GetMyAccountQuery:
    def __init__(self, engine: Engine, users: UserRepository, encryptor: FieldEncryptor) -> None:
        self._engine = engine
        self._users = users
        self._encryptor = encryptor

    def execute(self, user_id: uuid.UUID) -> AccountView | None:
        with self._engine.connect() as conn:
            account = self._users.get_account(conn, user_id)
            if account is None:
                return None
            return AccountView(
                user_id=account.user_id,
                email=account.email,
                full_name=account.full_name,
                phone=self._encryptor.decrypt(account.phone),
                city=account.city,
                home_address=account.home_address,
                is_admin=account.is_admin,
                has_requester_profile=self._users.requester_profile_id(conn, user_id) is not None,
                has_volunteer_profile=self._users.volunteer_profile_id(conn, user_id) is not None,
            )
