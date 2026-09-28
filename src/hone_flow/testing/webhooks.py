"""``WebhookServer``: a local HTTP server that records webhook requests, for testing notifications."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any


@dataclass(frozen=True)
class WebhookRequest:
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


class WebhookServer:
    """Records every POST in ``requests`` and answers with ``status`` (change it to script an outage).

    ``on_request`` is called with each request before the answer is sent.

    >>> with WebhookServer() as server:
    ...     server.url.startswith("http://127.0.0.1:")
    True
    """

    def __init__(
        self, *, status: int = 200, on_request: Callable[[WebhookRequest], None] | None = None
    ) -> None:
        self.status = status
        self.on_request = on_request
        self.requests: list[WebhookRequest] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request = WebhookRequest(self.path, dict(self.headers.items()), self.rfile.read(length))
                server.requests.append(request)
                if server.on_request is not None:
                    server.on_request(request)
                self.send_response(server.status)
                self.end_headers()
                self.wfile.write(b"ok" if server.status < 400 else b"error")

            def log_message(self, format: str, *args: Any) -> None:
                """Keep test output quiet."""

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="webhook-server", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/hook"

    def __enter__(self) -> WebhookServer:
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
