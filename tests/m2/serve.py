"""
Runs a FastAPI app as a real HTTP server on a free port, in a background
thread, so the tests can talk to it over real HTTP (the handoff and the
command-line tools use plain urllib, not a test client).

    server = Served(app)
    ... server.url ...
    server.close()
"""

import socket
import threading
import time

import uvicorn


class Served:
    def __init__(self, app):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started and time.time() < deadline:
            time.sleep(0.02)
        if not self.server.started:
            raise RuntimeError("test server did not start")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
