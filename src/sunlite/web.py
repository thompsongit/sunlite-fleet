from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from .codec import JsonObject
from .ipc import ControllerClient, ControllerGateway
from .security import (
    Identity,
    SecurityManager,
    SecuritySettings,
)

_ROOT = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=_ROOT / "templates")
_ASSET_VERSION = hashlib.sha256(
    (_ROOT / "static" / "app.js").read_bytes()
    + (_ROOT / "static" / "styles.css").read_bytes()
).hexdigest()[:12]
_TEMPLATES.env.globals["asset_version"] = _ASSET_VERSION
_COMMANDS = {
    "stop-all": "stop_all",
    "resume-all": "resume_all",
    "stop": "device.stop",
    "pause": "device.pause",
    "resume": "device.resume",
    "manual": "device.manual",
    "clear-fault": "device.clear_fault",
}


def create_app(
    gateway: ControllerGateway | None = None,
    security_settings: SecuritySettings | None = None,
) -> FastAPI:
    app = FastAPI(title="Sunlite Scheduler", docs_url=None, redoc_url=None)
    app.state.gateway = gateway or ControllerClient(
        os.getenv("SUNLITE_CONTROLLER_SOCKET", "/tmp/sunlite-controller.sock")
    )
    security = SecurityManager(security_settings or SecuritySettings.from_env())
    app.mount("/static", StaticFiles(directory=_ROOT / "static"), name="static")

    @app.middleware("http")
    async def secure_request(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        public = request.url.path == "/health" or request.url.path.startswith(
            ("/static/", "/favicon.ico")
        )
        cookie_value: str | None = None
        if not public:
            identity = security.identity()
            session_id, value, is_new = security.session(request)
            cookie_value = value if is_new else None
            request.state.identity = identity
            request.state.csrf_token = security.csrf_token(session_id)
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not security.valid_csrf(
                request, session_id
            ):
                csrf_response = JSONResponse(
                    {"detail": "Invalid CSRF token"}, status_code=403
                )
                security.apply_headers(csrf_response, request.url.path)
                return csrf_response
        response = await call_next(request)
        if cookie_value:
            security.set_session_cookie(response, cookie_value)
        security.apply_headers(response, request.url.path)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        return _TEMPLATES.TemplateResponse(
            request, "dashboard.html", {"page": "dashboard", "status": status}
        )

    @app.get("/devices/{device_id}", response_class=HTMLResponse)
    async def device_detail(request: Request, device_id: str) -> HTMLResponse:
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        device = next(
            (item for item in status.get("devices", []) if item.get("id") == device_id), None
        )
        if device is None:
            raise HTTPException(404, "Device not found")
        return _TEMPLATES.TemplateResponse(
            request,
            "device.html",
            {"page": "dashboard", "status": status, "device": device},
        )

    @app.get("/schedules", response_class=HTMLResponse)
    async def schedules_page(request: Request) -> HTMLResponse:
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        schedules = await _safe_request(app, {"action": "schedules.list"}, [])
        return _TEMPLATES.TemplateResponse(
            request,
            "schedules.html",
            {"page": "schedules", "status": status, "schedules": schedules},
        )

    @app.get("/schedules/new", response_class=HTMLResponse)
    @app.get("/schedules/{schedule_id}/edit", response_class=HTMLResponse)
    async def schedule_editor(request: Request, schedule_id: str | None = None) -> HTMLResponse:
        _require_operator(request)
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        schedule = None
        if schedule_id:
            schedule = await _safe_request(
                app, {"action": "schedules.get", "id": schedule_id}, None
            )
            if schedule is None:
                raise HTTPException(404, "Schedule not found")
        return _TEMPLATES.TemplateResponse(
            request,
            "schedule_form.html",
            {
                "page": "schedules",
                "status": status,
                "schedule": schedule,
                "timezone": status.get("timezone", "Africa/Johannesburg"),
            },
        )

    @app.get("/history", response_class=HTMLResponse)
    async def history_page(request: Request) -> HTMLResponse:
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        history = await _safe_request(app, {"action": "history"}, {"runs": [], "audit": []})
        return _TEMPLATES.TemplateResponse(
            request,
            "history.html",
            {"page": "history", "status": status, "history": history},
        )

    @app.get("/system", response_class=HTMLResponse)
    async def system_page(request: Request) -> HTMLResponse:
        status = await _safe_request(app, {"action": "status"}, _offline_status())
        system = await _safe_request(app, {"action": "system"}, {"controller": "offline"})
        return _TEMPLATES.TemplateResponse(
            request,
            "system.html",
            {"page": "system", "status": status, "system": system},
        )

    @app.get("/api/status")
    async def api_status() -> Any:
        return await _request(app, {"action": "status"})

    @app.get("/api/schedules")
    async def api_schedules() -> Any:
        return await _request(app, {"action": "schedules.list"})

    @app.get("/api/schedules/{schedule_id}")
    async def api_schedule(schedule_id: str) -> Any:
        return await _request(app, {"action": "schedules.get", "id": schedule_id})

    @app.post("/api/schedules")
    async def save_schedule(request: Request, payload: Annotated[JsonObject, Body()]) -> Any:
        return await _request(
            app,
            _mutation(
                request,
                {"action": "schedules.save", "schedule": payload},
            ),
        )

    @app.post("/api/schedules/preview")
    async def preview_schedule(payload: Annotated[JsonObject, Body()]) -> Any:
        return await _request(app, {"action": "schedules.preview", "schedule": payload})

    @app.delete("/api/schedules/{schedule_id}")
    async def delete_schedule(request: Request, schedule_id: str) -> Any:
        return await _request(
            app,
            _mutation(request, {"action": "schedules.delete", "id": schedule_id}),
        )

    @app.post("/api/commands/{command}")
    async def command(
        request: Request,
        command: str,
        payload: Annotated[JsonObject | None, Body()] = None,
    ) -> Any:
        action = _COMMANDS.get(command)
        if action is None:
            raise HTTPException(404, "Unknown command")
        return await _request(
            app, _mutation(request, {"action": action, **(payload or {})})
        )

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                try:
                    status = await _request(app, {"action": "status"})
                    yield f"data: {json.dumps(status, separators=(',', ':'))}\n\n"
                except HTTPException:
                    yield 'event: offline\ndata: {"healthy":false}\n\n'
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/health")
    async def health() -> Any:
        try:
            status = await _request(app, {"action": "status"})
        except HTTPException as error:
            raise HTTPException(503, "Controller unavailable") from error
        return {"status": "ok", "controller": status.get("healthy", False)}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> RedirectResponse:
        return RedirectResponse("/static/favicon.svg")

    return app


async def _request(app: FastAPI, payload: JsonObject) -> Any:
    gateway: ControllerGateway = app.state.gateway
    try:
        return await gateway.request(payload)
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise HTTPException(503 if isinstance(error, OSError) else 400, str(error)) from error


async def _safe_request(app: FastAPI, payload: JsonObject, fallback: Any) -> Any:
    try:
        return await _request(app, payload)
    except HTTPException:
        return fallback


def _offline_status() -> JsonObject:
    return {
        "healthy": False,
        "global_stop_latched": False,
        "timezone": "Africa/Johannesburg",
        "devices": [],
    }


def _identity(request: Request) -> Identity:
    identity = getattr(request.state, "identity", None)
    if not isinstance(identity, Identity):
        raise HTTPException(401, "Authentication required")
    return identity


def _require_operator(request: Request) -> None:
    if not _identity(request).can_operate:
        raise HTTPException(403, "Operator role required")


def _mutation(request: Request, payload: JsonObject) -> JsonObject:
    key = request.headers.get("Idempotency-Key", "").strip()
    if not 16 <= len(key) <= 128:
        raise HTTPException(400, "Idempotency-Key must contain 16 to 128 characters")
    return {
        **payload,
        "actor": _identity(request).email,
        "idempotency_key": key,
    }


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sunlite web application")
    parser.add_argument("--host", default=os.getenv("SUNLITE_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SUNLITE_WEB_PORT", "8000")))
    arguments = parser.parse_args()
    uvicorn.run(app, host=arguments.host, port=arguments.port)
