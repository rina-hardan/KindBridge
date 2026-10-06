from cryptography.fernet import Fernet


class FieldEncryptor:
    """App-level encryption for personal fields such as phone numbers."""

    def __init__(self, key: str):
        self._fernet = Fernet(key.encode("ascii"))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
