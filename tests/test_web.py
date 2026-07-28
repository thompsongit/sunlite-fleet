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
                        "phase": "running",
                        "handoff_seconds": 4,
                        "ocp_seconds": 60,
                        "run_elapsed_seconds": 70,
                        "first_light_at": "2026-07-12T08:01:04+00:00",
                    }
                ],
            }
        if action == "schedules.list":
            return [
                {
                    "id": "ready-a",
                    "device_id": "sunlite-a",
                    "name": "Ready run",
                    "kind": "on_demand",
                    "timezone": "Africa/Johannesburg",
                    "recovery_policy": "abort_if_interrupted",
                    "enabled": True,
                    "handoff_seconds": 4,
                    "ocp_seconds": 60,
                    "steps": [
                        {"offset_seconds": 60, "state": "on"},
                        {"offset_seconds": 75, "state": "off"},
                    ],
                }
            ]
        if action == "history":
            return {"runs": [], "audit": []}
        if action == "system":
            return {"controller": "healthy", "devices": [], "channels": []}
        if action == "schedules.preview":
            if payload["schedule"]["kind"] == "on_demand":
                return [
                    {"run_seconds": 0, "state": "off", "phase": "ocp"},
                    {"run_seconds": 60, "state": "on", "phase": "pattern"},
                ]
            return [{"at": "2026-07-12T08:00:00+00:00", "state": "on"}]
        if action in {"schedules.save", "schedules.delete", "schedules.launch"}:
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
    assert "12 Jul 2026, 10:10:00 SAST" in dashboard.text
    assert "Stop all" in dashboard.text
    assert "✓" in dashboard.text
    assert "HttpOnly" in dashboard.headers["set-cookie"]
    assert dashboard.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in dashboard.headers["content-security-policy"]
    assert re.search(r'/static/app\.js\?v=[0-9a-f]{12}', dashboard.text)
    static_response = client.get("/static/styles.css")
    assert static_response.headers["cache-control"] == "no-cache"
    styles = static_response.text
    assert "#faf7f7" in styles
    assert 'Raleway, "Open Sans"' in styles
    assert "health-line i" not in styles
    editor = client.get("/schedules/new").text
    assert "On-Demand Run" in editor
    assert editor.index("On-Demand Run") < editor.index("Scheduled Regular Cycle")
    assert editor.index("Scheduled Regular Cycle") < editor.index(
        "Scheduled Custom Timeline"
    )
    assert 'name="schedule-name"' in editor and 'id="schedule-error"' in editor
    assert "Handoff delay" in editor and "OCP period" in editor
    assert "Run time is measured from the beginning of the OCP period" in editor
    script = client.get("/static/app.js").text
    assert "localInputInZone" in script and "durationLabel" in script
    assert "Starts after handoff delay" in script
    schedules = client.get("/schedules").text
    assert "Launch run" in schedules and "Begin launch countdown" in schedules


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
            "starts_at_local": "2026-07-12T10:00:00",
            "timezone": "Africa/Johannesburg",
            "on_seconds": 60,
            "off_seconds": 60,
            "repeat_count": 1,
        },
    )
    assert preview.json()[0]["state"] == "on"
    assert gateway.requests[-1]["schedule"]["starts_at"] == "2026-07-12T08:00:00+00:00"

    relative = client.post(
        "/api/schedules/preview",
        headers=headers,
        json={
            "id": "ready-a",
            "device_id": "sunlite-a",
            "name": "Ready",
            "kind": "on_demand",
            "timezone": "Africa/Johannesburg",
            "handoff_seconds": 4,
            "ocp_seconds": 60,
            "steps": [
                {"offset_seconds": 60, "state": "on"},
                {"offset_seconds": 75, "state": "off"},
            ],
        },
    )
    assert relative.json()[1] == {
        "run_seconds": 60,
        "state": "on",
        "phase": "pattern",
    }

    launched = client.post(
        "/api/schedules/ready-a/launch",
        headers=headers,
        json={"handoff_seconds": 4, "ocp_seconds": 60},
    )
    assert launched.status_code == 200
    assert gateway.requests[-1]["action"] == "schedules.launch"
