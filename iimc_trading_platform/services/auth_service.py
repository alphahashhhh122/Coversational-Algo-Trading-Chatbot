from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from ..db import connect


Role = Literal["viewer", "researcher", "approver", "admin"]
ROLE_RANK = {
    "viewer": 0,
    "researcher": 1,
    "approver": 2,
    "admin": 3,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: Role
    authenticated: bool


class AuthService:
    def __init__(
        self,
        db_path: Path,
        *,
        secret: str,
        session_ttl_minutes: int = 480,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("Authentication secret must be at least 32 characters")
        self.db_path = db_path
        self.secret = secret.encode("utf-8")
        self.session_ttl = timedelta(minutes=session_ttl_minutes)

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: Role,
    ) -> Principal:
        username = username.strip().lower()
        _validate_username(username)
        _validate_password(password)
        if role not in ROLE_RANK:
            raise ValueError(f"Unsupported role: {role}")
        user_id = f"user_{uuid.uuid4().hex[:12]}"
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO app_users VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    user_id,
                    username,
                    _hash_password(password),
                    role,
                    True,
                    now,
                    now,
                ],
            )
        finally:
            con.close()
        return Principal(user_id, username, role, True)

    def login(self, username: str, password: str) -> dict:
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT user_id, username, password_hash, role, active
                FROM app_users
                WHERE username = ?
                """,
                [username.strip().lower()],
            ).fetchone()
        finally:
            con.close()
        if row is None or not row[4] or not _verify_password(password, row[2]):
            raise ValueError("Invalid username or password")
        principal = Principal(row[0], row[1], row[3], True)
        expires_at = utc_now() + self.session_ttl
        nonce = secrets.token_urlsafe(24)
        payload = {
            "uid": principal.user_id,
            "exp": int(
                expires_at.replace(tzinfo=timezone.utc).timestamp()
            ),
            "nonce": nonce,
        }
        encoded = _urlsafe_json(payload)
        signature = _sign(encoded, self.secret)
        token = f"{encoded}.{signature}"
        now = utc_now()
        con = connect(self.db_path)
        try:
            con.execute(
                """
                INSERT INTO auth_sessions VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    _token_hash(token),
                    principal.user_id,
                    now,
                    expires_at,
                    None,
                    now,
                ],
            )
        finally:
            con.close()
        return {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": expires_at,
            "user": principal,
        }

    def authenticate(self, token: str) -> Principal:
        try:
            encoded, signature = token.split(".", 1)
        except ValueError as exc:
            raise ValueError("Invalid authentication token") from exc
        if not hmac.compare_digest(signature, _sign(encoded, self.secret)):
            raise ValueError("Invalid authentication token")
        try:
            payload = json.loads(
                base64.urlsafe_b64decode(_pad(encoded)).decode("utf-8")
            )
        except Exception as exc:
            raise ValueError("Invalid authentication token") from exc
        now = utc_now()
        expires_at = datetime.fromtimestamp(
            int(payload["exp"]),
            tz=timezone.utc,
        ).replace(tzinfo=None)
        if expires_at <= now:
            raise ValueError("Authentication token has expired")
        con = connect(self.db_path)
        try:
            row = con.execute(
                """
                SELECT u.user_id, u.username, u.role, u.active,
                       s.expires_at, s.revoked_at
                FROM auth_sessions AS s
                JOIN app_users AS u ON u.user_id = s.user_id
                WHERE s.session_token_hash = ?
                  AND u.user_id = ?
                """,
                [_token_hash(token), payload.get("uid")],
            ).fetchone()
            if (
                row is None
                or not row[3]
                or row[5] is not None
                or row[4] <= now
            ):
                raise ValueError("Authentication session is not active")
            con.execute(
                """
                UPDATE auth_sessions
                SET last_seen_at = ?
                WHERE session_token_hash = ?
                """,
                [now, _token_hash(token)],
            )
        finally:
            con.close()
        return Principal(row[0], row[1], row[2], True)

    def logout(self, token: str) -> None:
        con = connect(self.db_path)
        try:
            con.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE session_token_hash = ?
                  AND revoked_at IS NULL
                """,
                [utc_now(), _token_hash(token)],
            )
        finally:
            con.close()

    @staticmethod
    def require_role(principal: Principal, minimum: Role) -> None:
        if ROLE_RANK[principal.role] < ROLE_RANK[minimum]:
            raise PermissionError(
                f"Role {minimum} or higher is required"
            )


def _validate_username(username: str) -> None:
    if len(username) < 3 or len(username) > 64:
        raise ValueError("Username must be between 3 and 64 characters")
    if not all(character.isalnum() or character in "._-" for character in username):
        raise ValueError("Username contains unsupported characters")


def _validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if not any(character.isalpha() for character in password):
        raise ValueError("Password must contain a letter")
    if not any(character.isdigit() for character in password):
        raise ValueError("Password must contain a number")


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.b64encode(salt).decode()}$"
        f"{base64.b64encode(digest).decode()}"
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, digest_text = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_text)
        expected = base64.b64decode(digest_text)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iteration_text),
        )
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)


def _urlsafe_json(payload: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode().rstrip("=")


def _pad(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode()


def _sign(encoded: str, secret: bytes) -> str:
    return base64.urlsafe_b64encode(
        hmac.new(secret, encoded.encode(), hashlib.sha256).digest()
    ).decode().rstrip("=")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
