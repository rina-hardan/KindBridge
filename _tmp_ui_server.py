import os
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine

from app import create_app
from app.config import Config
from app.repositories.tables import metadata

db = Path(os.environ["TEMP"]) / "kindbridge-request-check.db"
if db.exists():
    db.unlink()
engine = create_engine("sqlite:///" + db.resolve().as_posix())
metadata.create_all(engine)
config = Config(
    database_url="sqlite:///" + db.resolve().as_posix(),
    jwt_secret="request-check-secret-key-32chars-min",
    encryption_key=Fernet.generate_key().decode(),
    bcrypt_rounds=4,
    cookie_secure=False,
    testing=True,
)
app = create_app(config=config, engine=engine)
app.run(host="127.0.0.1", port=8770, debug=False, use_reloader=False)
