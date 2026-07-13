import re
from typing import Any

from fastapi.testclient import TestClient

from sunlite.security import AuthenticationError, SecuritySettings
from sunlite.web import create_app


class FakeVerifier:
    async def verify(self, token: str) -> dict[str, Any]:
        if token == "invalid":
            raise AuthenticationError("invalid token")
        return {"email": token, "sub": f"subject:{token}"}


class FakeGateway:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def request(self, payload: dict[str, Any]) -> Any:
        self.requests.append(payload)
        if payload["action"] in {"status", "stop_all"}:
            return {
                "healthy": True,
                "timezone": "Africa/Johannesburg",
                "global_stop_latched": False,
                "devices": [],
            }
        raise RuntimeError("unsupported action")


def settings() -> SecuritySettings:
    return SecuritySettings(
        access_required=True,
        team_domain="https://lab.cloudflareaccess.com",
        audience="sunlite-audience",
        session_secret="s" * 32,
        operator_emails=frozenset({"operator@example.com"}),
        cookie_secure=True,
        allowed_hosts=("testserver",),
    )


def access_client(gateway: FakeGateway | None = None) -> TestClient:
    return TestClient(
        create_app(gateway or FakeGateway(), settings(), FakeVerifier()),
        base_url="https://testserver",
    )


def test_access_requires_valid_identity_and_roles() -> None:
    client = access_client()
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"Cf-Access-Jwt-Assertion": "invalid"}).status_code == 401

    identity = {"Cf-Access-Jwt-Assertion": "viewer@example.com"}
    page = client.get("/", headers=identity)
    assert page.status_code == 200
    assert 'data-command="stop-all"' not in page.text
    assert client.get("/schedules/new", headers=identity).status_code == 403
    assert client.post("/api/commands/stop-all", headers=identity).status_code == 403


def test_operator_mutation_uses_csrf_and_identity() -> None:
    gateway = FakeGateway()
    client = access_client(gateway)
    access = {"Cf-Access-Jwt-Assertion": "operator@example.com"}
    page = client.get("/", headers=access)
    assert "Secure" in page.headers["set-cookie"]
    assert "SameSite=strict" in page.headers["set-cookie"]
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert token
    headers = {
        **access,
        "X-CSRF-Token": token.group(1),
        "Idempotency-Key": "access-request-0001",
    }
    assert client.post("/api/commands/stop-all", headers=access).status_code == 403
    assert client.post("/api/commands/stop-all", headers=headers).status_code == 200
    assert gateway.requests[-1]["actor"] == "operator@example.com"
