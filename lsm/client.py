"""
Lightweight Python Client SDK for interacting with the LSM Key-Value Server over HTTP.
Uses standard library urllib (zero external dependencies).
"""

from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


class LSMClientError(Exception):
    """Raised when an LSM API request fails."""
    pass


class LSMClient:
    """Client for connecting to a remote or local LSM Database Server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read().decode("utf-8")
                if "application/json" in content_type:
                    return json.loads(raw)
                return raw
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            err_msg = e.read().decode("utf-8")
            raise LSMClientError(f"HTTP {e.code}: {err_msg}") from e
        except urllib.error.URLError as e:
            raise LSMClientError(f"Failed to connect to LSM Server at {self.base_url}: {e.reason}") from e

    def put(self, key: str, value: Any) -> bool:
        """Stores a key-value pair."""
        val_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        res = self._request("POST", "/api/v1/keys", {"key": key, "value": val_str})
        return res is not None and res.get("status") == "ok"

    def get(self, key: str) -> Optional[str]:
        """Retrieves value for key or None if not found."""
        res = self._request("GET", f"/api/v1/keys/{urllib.parse.quote(key)}")
        if res is None:
            return None
        return res.get("value")

    def get_json(self, key: str) -> Optional[Any]:
        """Retrieves and JSON-deserializes value for key."""
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    def delete(self, key: str) -> bool:
        """Deletes a key (writes a tombstone)."""
        res = self._request("DELETE", f"/api/v1/keys/{urllib.parse.quote(key)}")
        return res is not None and res.get("status") == "ok"

    def scan(self, start_key: Optional[str] = None, end_key: Optional[str] = None, limit: int = 100) -> List[Tuple[str, str]]:
        """Scans keys in range [start_key, end_key)."""
        params = {"limit": str(limit)}
        if start_key:
            params["start"] = start_key
        if end_key:
            params["end"] = end_key
        query = urllib.parse.urlencode(params)
        res = self._request("GET", f"/api/v1/scan?{query}")
        if not res or "results" not in res:
            return []
        return [(item["key"], item["value"]) for item in res["results"]]

    def flush(self) -> bool:
        """Forces flush of active MemTable to an on-disk SSTable."""
        res = self._request("POST", "/api/v1/flush")
        return res is not None and res.get("status") == "ok"

    def compact(self) -> bool:
        """Triggers SSTable merge compaction."""
        res = self._request("POST", "/api/v1/compact")
        return res is not None and res.get("status") == "ok"

    def stats(self) -> Dict[str, Any]:
        """Fetches telemetry and engine statistics."""
        return self._request("GET", "/api/v1/stats")

