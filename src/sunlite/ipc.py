from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

from .codec import JsonObject
from .controller import ControllerService


class ControllerGateway(Protocol):
    async def request(self, payload: JsonObject) -> Any: ...


class ControllerClient:
    def __init__(self, socket_path: str | Path) -> None:
        self.socket_path = str(socket_path)

    async def request(self, payload: JsonObject) -> Any:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        try:
            writer.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
            await writer.drain()
            line = await reader.readline()
            if not line:
                raise RuntimeError("controller closed the connection")
            response = json.loads(line)
            if not isinstance(response, dict):
                raise RuntimeError("invalid controller response")
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error", "controller request failed")))
            return response.get("data")
        finally:
            writer.close()
            await writer.wait_closed()


class ControllerSocketServer:
    def __init__(self, service: ControllerService, socket_path: str | Path) -> None:
        self.service = service
        self.socket_path = Path(socket_path)
        self._server: asyncio.Server | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.socket_path), limit=256 * 1024
        )
        os.chmod(self.socket_path, 0o660)

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("controller socket server is not started")
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            line = await reader.readline()
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            async with self._lock:
                data = self.service.handle(request)
            response = {"ok": True, "data": data}
        except Exception as error:
            response = {"ok": False, "error": str(error)}
        writer.write(json.dumps(response, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()


async def run_controller(service: ControllerService, socket_path: str | Path) -> None:
    service.initialize()
    server = ControllerSocketServer(service, socket_path)
    await server.start()
    scheduler = asyncio.create_task(service.scheduler_loop())
    try:
        await server.serve_forever()
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
        await server.close()
        service.shutdown()
