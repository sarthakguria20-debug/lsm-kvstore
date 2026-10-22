"""
Unit tests for LSMServerHandler using mock I/O streams.
Executes 100% offline without requiring TCP network socket permissions.
"""

from __future__ import annotations
import io
import json
import tempfile
import unittest
from pathlib import Path

from lsm.engine import LSMTreeEngine # type: ignore
from lsm.server import LSMServerHandler # type: ignore
from lsm.wal import SyncMode # type: ignore


class MockSocket:
    """Emulates a socket for BaseHTTPRequestHandler without opening network ports."""
    def __init__(self, data: bytes):
        self._rfile = io.BytesIO(data)
        self._wfile = io.BytesIO()

    def makefile(self, mode: str, bufsize: int = -1):
        if "r" in mode:
            return self._rfile
        elif "w" in mode or "b" in mode:
            return self._wfile
        raise ValueError(f"Unsupported mode: {mode}")

    def sendall(self, data: bytes):
        self._wfile.write(data)


def simulate_http_call(handler_cls, method: str, path: str, body: dict = None) -> tuple[int, dict | str]: # type: ignore
    """Simulates an HTTP request against LSMServerHandler via in-memory byte streams."""
    body_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
    raw_req = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
    if body is not None:
        raw_req += f"Content-Type: application/json\r\nContent-Length: {len(body_bytes)}\r\n"
    raw_req += "\r\n"

    request_bytes = raw_req.encode("utf-8") + body_bytes
    sock = MockSocket(request_bytes)

    # Initialize handler with dummy client address and mock socket
    handler = handler_cls(sock, ("127.0.0.1", 12345), None)

    # Parse response
    output_bytes = sock._wfile.getvalue()
    header_end = output_bytes.find(b"\r\n\r\n")
    if header_end == -1:
        return 500, ""

    header_text = output_bytes[:header_end].decode("utf-8", errors="replace")
    body_data = output_bytes[header_end + 4:].decode("utf-8", errors="replace")

    status_line = header_text.splitlines()[0]
    status_code = int(status_line.split()[1])

    try:
        return status_code, json.loads(body_data)
    except Exception:
        return status_code, body_data


class TestServerHandler(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir = Path(self.temp_dir.name)
        self.db = LSMTreeEngine(self.db_dir, sync_mode=SyncMode.ALWAYS)
        LSMServerHandler.db = self.db

    def tearDown(self):
        if LSMServerHandler.db:
            LSMServerHandler.db.close()
            LSMServerHandler.db = None
        self.temp_dir.cleanup()

    def test_dashboard_html_served(self):
        status, body = simulate_http_call(LSMServerHandler, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("LSM-Tree Database Engine", body)

    def test_crud_endpoints(self):
        # 1. Put
        status, data = simulate_http_call(
            LSMServerHandler, "POST", "/api/v1/keys", {"key": "api_user", "value": "Alice"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok") # type: ignore

        # 2. Get
        status, data = simulate_http_call(LSMServerHandler, "GET", "/api/v1/keys/api_user")
        self.assertEqual(status, 200)
        self.assertEqual(data["value"], "Alice") # type: ignore

        # 3. Stats
        status, data = simulate_http_call(LSMServerHandler, "GET", "/api/v1/stats")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["current_lsn"], 1) # type: ignore

        # 4. Scan
        status, data = simulate_http_call(LSMServerHandler, "GET", "/api/v1/scan?start=api_&end=api_~")
        self.assertEqual(status, 200)
        self.assertGreater(data["count"], 0) # type: ignore

        # 5. Delete
        status, data = simulate_http_call(LSMServerHandler, "DELETE", "/api/v1/keys/api_user")
        self.assertEqual(status, 200)

        # 6. Verify 404
        status, data = simulate_http_call(LSMServerHandler, "GET", "/api/v1/keys/api_user")
        self.assertEqual(status, 404)

    def test_batch_and_flush_endpoints(self):
        batch_payload = {
            "ops": [
                {"type": "put", "key": "batch_1", "value": "v1"},
                {"type": "put", "key": "batch_2", "value": "v2"},
            ]
        }
        status, data = simulate_http_call(LSMServerHandler, "POST", "/api/v1/batch", batch_payload)
        self.assertEqual(status, 200)

        # Flush
        status, data = simulate_http_call(LSMServerHandler, "POST", "/api/v1/flush")
        self.assertEqual(status, 200)

        # SSTables list
        status, data = simulate_http_call(LSMServerHandler, "GET", "/api/v1/sstables")
        self.assertEqual(status, 200)
        self.assertGreater(len(data["sstables"]), 0) # type: ignore

    def test_crash_simulation_endpoints(self):
        # Simulated power cut
        status, data = simulate_http_call(LSMServerHandler, "POST", "/api/v1/simulate/crash-durability")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "success") # type: ignore

        # Simulated torn write
        status, data = simulate_http_call(LSMServerHandler, "POST", "/api/v1/simulate/torn-write")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "success") # type: ignore


if __name__ == "__main__":
    unittest.main()
