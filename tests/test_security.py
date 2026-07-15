import re
from typing import Any

from fastapi.testclient import TestClient

from sunlite.security import SecuritySettings
from sunlite.web import create_app


class FakeGateway:
    async def request(self, payload: dict[str, Any]) -> Any:
        return {
            "healthy": True,
            "timezone": "Africa/Johannesburg",
            "global_stop_latched": False,
            "devices": [],
        }


def test_local_access_keeps_session_and_csrf_protection() -> None:
    client = TestClient(create_app(FakeGateway(), SecuritySettings("s" * 32)))
    page = client.get("/")
    token = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert page.status_code == 200 and token
    assert client.post("/api/commands/stop-all").status_code == 403
    response = client.post(
        "/api/commands/stop-all",
        headers={
            "X-CSRF-Token": token.group(1),
            "Idempotency-Key": "local-request-0001",
        },
    )
    assert response.status_code == 200
