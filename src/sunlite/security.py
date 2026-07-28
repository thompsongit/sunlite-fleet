from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass

from fastapi import Request
from starlette.responses import Response


@dataclass(frozen=True, slots=True)
class Identity:
    email: str = "local-operator@sunlite.invalid"
    subject: str = "local"

    @property
    def can_operate(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    session_secret: str

    def __post_init__(self) -> None:
        if len(self.session_secret) < 32:
            raise ValueError("SUNLITE_SESSION_SECRET must contain at least 32 characters")

    @classmethod
    def from_env(cls) -> SecuritySettings:
        return cls(os.getenv("SUNLITE_SESSION_SECRET") or secrets.token_urlsafe(48))


class SecurityManager:
    cookie_name = "sunlite_session"

    def __init__(self, settings: SecuritySettings) -> None:
        self.settings = settings
        self._secret = settings.session_secret.encode()

    @staticmethod
    def identity() -> Identity:
        return Identity()

    def session(self, request: Request) -> tuple[str, str, bool]:
        existing = request.cookies.get(self.cookie_name, "")
        session_id = self._validate_session(existing)
        if session_id:
            return session_id, existing, False
        session_id = secrets.token_urlsafe(32)
        return session_id, f"{session_id}.{self._sign(session_id)}", True

    def csrf_token(self, session_id: str) -> str:
        return self._sign(f"csrf:{session_id}")

    def valid_csrf(self, request: Request, session_id: str) -> bool:
        supplied = request.headers.get("X-CSRF-Token", "")
        return hmac.compare_digest(supplied, self.csrf_token(session_id))

    def set_session_cookie(self, response: Response, value: str) -> None:
        response.set_cookie(
            self.cookie_name,
            value,
            max_age=8 * 60 * 60,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )

    @staticmethod
    def apply_headers(response: Response, path: str) -> None:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' "
            "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cache-Control"] = (
            "no-cache" if path.startswith("/static/") else "no-store"
        )

    def _validate_session(self, value: str) -> str | None:
        try:
            session_id, signature = value.rsplit(".", 1)
        except ValueError:
            return None
        if len(session_id) < 24 or not hmac.compare_digest(signature, self._sign(session_id)):
            return None
        return session_id

    def _sign(self, value: str) -> str:
        digest = hmac.new(self._secret, value.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
