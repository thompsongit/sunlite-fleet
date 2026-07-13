from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

import jwt
from fastapi import Request
from jwt import PyJWKClient
from starlette.responses import Response


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Identity:
    email: str
    subject: str
    role: Role

    @property
    def can_operate(self) -> bool:
        return self.role in {Role.OPERATOR, Role.ADMIN}


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    access_required: bool
    team_domain: str
    audience: str
    session_secret: str
    admin_emails: frozenset[str] = frozenset()
    operator_emails: frozenset[str] = frozenset()
    cookie_secure: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "testserver")

    def __post_init__(self) -> None:
        if len(self.session_secret) < 32:
            raise ValueError("SUNLITE_SESSION_SECRET must contain at least 32 characters")
        if self.access_required and (
            not self.team_domain.startswith("https://") or not self.audience
        ):
            raise ValueError("Cloudflare team domain and audience are required")
        if not self.allowed_hosts:
            raise ValueError("at least one allowed host is required")

    @classmethod
    def from_env(cls) -> SecuritySettings:
        required = _env_bool("SUNLITE_ACCESS_REQUIRED", False)
        configured_secret = os.getenv("SUNLITE_SESSION_SECRET")
        if required and not configured_secret:
            raise ValueError("SUNLITE_SESSION_SECRET is required with Cloudflare Access")
        secret = configured_secret or secrets.token_urlsafe(48)
        return cls(
            access_required=required,
            team_domain=os.getenv("SUNLITE_CF_TEAM_DOMAIN", "").rstrip("/"),
            audience=os.getenv("SUNLITE_CF_AUDIENCE", ""),
            session_secret=secret,
            admin_emails=_email_set("SUNLITE_ADMIN_EMAILS"),
            operator_emails=_email_set("SUNLITE_OPERATOR_EMAILS"),
            cookie_secure=_env_bool("SUNLITE_COOKIE_SECURE", required),
            allowed_hosts=tuple(
                item.strip()
                for item in os.getenv(
                    "SUNLITE_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver"
                ).split(",")
                if item.strip()
            ),
        )


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> dict[str, Any]: ...


class AuthenticationError(Exception):
    pass


class CloudflareAccessVerifier:
    def __init__(self, team_domain: str, audience: str) -> None:
        self.team_domain = team_domain.rstrip("/")
        self.audience = audience
        self._jwks = PyJWKClient(f"{self.team_domain}/cdn-cgi/access/certs", cache_jwk_set=True)

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.team_domain,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "email"]},
            )
        except jwt.PyJWTError as error:
            raise AuthenticationError("invalid Cloudflare Access token") from error
        if not isinstance(claims, dict):
            raise AuthenticationError("invalid Cloudflare Access claims")
        return claims


class SecurityManager:
    cookie_name = "sunlite_session"

    def __init__(
        self, settings: SecuritySettings, verifier: TokenVerifier | None = None
    ) -> None:
        self.settings = settings
        self.verifier = verifier or (
            CloudflareAccessVerifier(settings.team_domain, settings.audience)
            if settings.access_required
            else None
        )
        self._secret = settings.session_secret.encode()

    async def authenticate(self, request: Request) -> Identity:
        if not self.settings.access_required:
            return Identity("local-operator@sunlite.invalid", "local", Role.ADMIN)
        token = request.headers.get("Cf-Access-Jwt-Assertion")
        if not token or self.verifier is None:
            raise AuthenticationError("Cloudflare Access token required")
        claims = await self.verifier.verify(token)
        email = str(claims.get("email", "")).strip().lower()
        subject = str(claims.get("sub", "")).strip()
        if not email or not subject:
            raise AuthenticationError("Cloudflare Access identity is incomplete")
        if email in self.settings.admin_emails:
            role = Role.ADMIN
        elif email in self.settings.operator_emails:
            role = Role.OPERATOR
        else:
            role = Role.VIEWER
        return Identity(email, subject, role)

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
            secure=self.settings.cookie_secure,
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
        if not path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"

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


def _email_set(name: str) -> frozenset[str]:
    return frozenset(
        item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")
