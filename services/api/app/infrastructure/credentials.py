"""Development-only encryption for workspace model credentials.

Do not sync the generated .local master key to source control. Production must
use an external secret manager/KMS, not this local-file key.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .sqlite.connection import db_path


def _master_key() -> bytes:
    explicit = os.getenv("STUDIO_MODEL_ENCRYPTION_KEY", "").strip()
    if explicit:
        key = explicit.encode("ascii")
        Fernet(key)  # Validate before using it.
        return key
    location: Path = db_path().with_name("model_master.key")
    location.parent.mkdir(parents=True, exist_ok=True)
    try:
        with location.open("xb") as output:
            try:
                os.chmod(location, 0o600)
            except OSError:
                pass  # Windows ACL depends on account/profile.
            output.write(Fernet.generate_key())
    except FileExistsError:
        pass
    key = location.read_bytes().strip()
    Fernet(key)
    return key


def encrypt_key(secret: str) -> str:
    if not secret:
        return ""
    return Fernet(_master_key()).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_key(token: str) -> str:
    if not token:
        return ""
    try:
        return Fernet(_master_key()).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("模型凭据无法解密，请检查本地加密主密钥是否已变更") from exc
