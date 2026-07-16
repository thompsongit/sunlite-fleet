import re
from typing import Any

from fastapi.testclient import TestClient

from sunlite.web import create_app


class FakeGateway:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def request(self, payload: dict[str, Any]) -> Any:
        self.requests.append(payload)
        action = payload["action"]
        if (
            action == "status"
            or action.startswith("device.")
            or action in {"stop_all", "resume_all"}
        ):
            return {
                "healthy": True,
                "timezone": "Africa/Johannesburg",
                "global_stop_latched": False,
                "devices": [
                    {
                        "id": "sunlite-a",
                        "name": "Sunlite 11002 - A",
                        "commanded_state": "on",
                        "mode": "automatic_on",
                        "fault": None,
                        "active_schedule_id": "cycle-a",
                        "active_schedule_name": "Cell stability cycle",
                        "updated_at": "2026-07-12T08:00:00+00:00",
                        "next_transition": {
                            "at": "2026-07-12T08:10:00+00:00",
                            "state": "off",
                        },
                    }
                ],
            }
        if action == "schedules.list":
            return []
        if action == "history":
            return {"runs": [], "audit": []}
        if action == "system":
            return {"controller": "healthy", "devices": [], "channels": []}
        if action == "schedules.preview":
            return [{"at": "2026-07-12T08:00:00+00:00", "state": "on"}]
        if action in {"schedules.save", "schedules.delete"}:
            return {"healthy": True, "devices": []}
        raise RuntimeError(f"unsupported fake action: {action}")


def mutation_headers(client: TestClient) -> dict[str, str]:
    page = client.get("/")
    match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
    assert match
    return {
        "X-CSRF-Token": match.group(1),
        "Idempotency-Key": "test-request-0001",
    }


def test_fleet_pages_render() -> None:
    client = TestClient(create_app(FakeGateway()))
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Sunlite 11002 - A" in dashboard.text
    assert "Stop all" in dashboard.text
    assert "✓" in dashboard.text
    assert "HttpOnly" in dashboard.headers["set-cookie"]
    assert dashboard.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]
    styles = client.get("/static/styles.css").text
    assert "#faf7f7" in styles
    assert 'Raleway, "Open Sans"' in styles
    assert "health-line i" not in styles
    editor = client.get("/schedules/new").text
    assert "Custom timeline" in editor
    assert 'name="schedule-name"' in editor and 'id="schedule-error"' in editor
    assert editor.count('step="any"') == 2
    script = client.get("/static/app.js").text
    assert "setControlsEnabled" in script and "must end OFF" in script and 'step="any"' in script


def test_web_commands_and_preview_use_gateway() -> None:
    gateway = FakeGateway()
    client = TestClient(create_app(gateway))
    headers = mutation_headers(client)
    response = client.post(
        "/api/commands/manual",
        headers=headers,
        json={
            "device_id": "sunlite-a",
            "state": "off",
            "duration_seconds": 60,
            "reason": "sample change",
        },
    )
    assert response.status_code == 200
    assert gateway.requests[-1]["action"] == "device.manual"
    assert gateway.requests[-1]["actor"] == "local-operator@sunlite.invalid"
    assert gateway.requests[-1]["idempotency_key"] == "test-request-0001"

    preview = client.post(
        "/api/schedules/preview",
        headers=headers,
        json={
            "id": "cycle-a",
            "device_id": "sunlite-a",
            "name": "Cycle",
            "kind": "regular",
            "starts_at": "2026-07-12T08:00:00+00:00",
            "timezone": "Africa/Johannesburg",
            "on_seconds": 60,
            "off_seconds": 60,
            "repeat_count": 1,
        },
    )
    assert preview.json()[0]["state"] == "on"
