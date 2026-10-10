"""Password verification and session issuance independent of HTTP."""

import hashlib
import hmac
import secrets

from ..domain.errors import AgentNotFound
from ..infrastructure.sqlite import identity_repository as identity

SCRYPT_N, SCRYPT_R, SCRYPT_P = 1 << 14, 8, 1


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=32,
    )
    return "$".join(
        ["scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P), salt.hex(), digest.hex()]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        name, n, r, p, salt, expected = stored.split("$")
        if name != "scrypt" or (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=32,
        )
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def register(email: str, password: str) -> tuple[dict, str]:
    user, _ = identity.create_user_with_personal_workspace(email, password_hash(password))
    token = identity.create_session(user["id"])
    return {"user": user, "workspaces": identity.list_workspaces(user["id"])}, token


def login(email: str, password: str) -> tuple[dict, str]:
    user = identity.get_user_by_email(email)
    stored = user["password_hash"] if user else password_hash("dummy-password-never-used")
    verified = verify_password(password, stored)
    if user is None or not verified:
        raise AgentNotFound("邮箱或密码不正确")
    token = identity.create_session(user["id"])
    return {
        "user": {"id": user["id"], "email": user["email"]},
        "workspaces": identity.list_workspaces(user["id"]),
    }, token
