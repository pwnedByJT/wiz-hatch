"""Minimal dependency-free health server for container probes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

ReadinessCheck = Callable[[], bool]


class HealthServer:
    """Serve fixed liveness and readiness endpoints over HTTP/1.1."""

    def __init__(self, host: str, port: int, readiness_check: ReadinessCheck) -> None:
        self._host = host
        self._port = port
        self._readiness_check = readiness_check
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start listening when the server is not already active."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=self._port,
            limit=8_192,
        )

    async def close(self) -> None:
        """Stop accepting connections and wait for the socket to close."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status = "400 Bad Request"
        body = b'{"status":"bad_request"}\n'
        try:
            async with asyncio.timeout(2):
                request_line = await reader.readline()
            method, target, _protocol = request_line.decode("ascii").strip().split()
            path = target.partition("?")[0]
            if method != "GET":
                status = "405 Method Not Allowed"
                body = b'{"status":"method_not_allowed"}\n'
            elif path == "/healthz":
                status = "200 OK"
                body = b'{"status":"ok"}\n'
            elif path == "/readyz":
                if self._readiness_check():
                    status = "200 OK"
                    body = b'{"status":"ready"}\n'
                else:
                    status = "503 Service Unavailable"
                    body = b'{"status":"not_ready"}\n'
            else:
                status = "404 Not Found"
                body = b'{"status":"not_found"}\n'
        except (TimeoutError, UnicodeDecodeError, ValueError):
            pass

        response = (
            f"HTTP/1.1 {status}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + body
        writer.write(response)
        with suppress(ConnectionError):
            await writer.drain()
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()
