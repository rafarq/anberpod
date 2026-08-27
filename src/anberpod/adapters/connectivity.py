"""Background connectivity probe safe to poll from the 30 fps UI loop.

``is_online()`` must never block the SDL frame loop, so the actual
network check (a short TCP connect attempt) runs on a daemon thread at
a low frequency and the main thread only ever reads a cached boolean.

The check target is a bare TCP connect to a stable public DNS resolver
(Cloudflare 1.1.1.1:443) rather than an HTTP request: it is cheap, does
not depend on this project's HTTPS/redirect/content policy, and giving
a fixed IP literal avoids doing DNS resolution just to determine
whether DNS/network already works.
"""

from __future__ import annotations

import socket
import threading
import time


class SystemConnectivityProbe:
    def __init__(
        self,
        *,
        host: str = "1.1.1.1",
        port: int = 443,
        connect_timeout_seconds: float = 2.0,
        check_interval_seconds: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout_seconds = connect_timeout_seconds
        self._check_interval_seconds = check_interval_seconds
        self._lock = threading.Lock()
        self._online = self._probe_once()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def is_online(self) -> bool:
        with self._lock:
            return self._online

    def shutdown(self) -> None:
        self._stop.set()

    def _probe_once(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=self._connect_timeout_seconds):
                return True
        except OSError:
            return False

    def _run(self) -> None:
        while not self._stop.wait(self._check_interval_seconds):
            result = self._probe_once()
            with self._lock:
                self._online = result
