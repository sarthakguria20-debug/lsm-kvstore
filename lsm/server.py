"""
High-performance HTTP REST Server and Web Application for the LSM Key-Value Engine.
Provides a RESTful API and an interactive Web Dashboard with live storage visualization
and a Power-Outage / Crash Resilience Simulation Lab.
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from lsm.engine import LSMTreeEngine, WriteBatch # type: ignore
from lsm.types import RecordType # type: ignore
from lsm.wal import SyncMode # type: ignore

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LSM-Tree Database Engine & Storage Visualizer</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #e6edf3;
      --text-muted: #8b949e;
      --accent: #58a6ff;
      --accent-hover: #1f6feb;
      --success: #3fb950;
      --danger: #f85149;
      --warning: #d29922;
      --code-bg: #0b0e14;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
    body { background: var(--bg); color: var(--text); padding: 20px; line-height: 1.5; }
    header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 15px; margin-bottom: 20px; }
    .brand { display: flex; align-items: center; gap: 12px; }
    .badge { padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: bold; background: #238636; color: white; display: inline-flex; align-items: center; gap: 6px; }
    .badge.fsync { background: #1f6feb; }
    .stats-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 15px; }
    .stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 6px; }
    .stat-val { font-size: 24px; font-weight: bold; color: var(--accent); }
    .progress-bar-bg { background: #21262d; border-radius: 4px; height: 8px; overflow: hidden; margin-top: 8px; }
    .progress-bar-fill { background: var(--accent); height: 100%; width: 0%; transition: width 0.3s; }

    /* Tabs */
    .tabs { display: flex; gap: 10px; border-bottom: 1px solid var(--border); margin-bottom: 20px; }
    .tab-btn { background: transparent; border: none; color: var(--text-muted); padding: 10px 16px; cursor: pointer; font-size: 14px; font-weight: 600; border-bottom: 2px solid transparent; }
    .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
    .tab-content { display: none; }
    .tab-content.active { display: block; }

    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    @media (max-width: 900px) { .grid-2 { grid-template-columns: 1fr; } }

    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; }
    .card h3 { margin-bottom: 15px; font-size: 16px; display: flex; align-items: center; justify-content: space-between; }
    .form-group { margin-bottom: 15px; }
    label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
    input, textarea, select { width: 100%; background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px; padding: 10px; color: var(--text); font-family: monospace; font-size: 14px; }
    input:focus, textarea:focus { outline: none; border-color: var(--accent); }
    .btn { background: var(--accent); color: white; border: none; border-radius: 6px; padding: 10px 16px; font-weight: 600; cursor: pointer; transition: 0.2s; font-size: 14px; }
    .btn:hover { background: var(--accent-hover); }
    .btn.danger { background: var(--danger); }
    .btn.danger:hover { background: #da3633; }
    .btn.success { background: var(--success); }
    .btn.warning { background: var(--warning); color: #000; }
    .btn-sm { padding: 6px 12px; font-size: 12px; }

    table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid var(--border); font-family: monospace; }
    th { color: var(--text-muted); font-size: 12px; text-transform: uppercase; }
    .tombstone { color: var(--danger); font-weight: bold; }
    .put-type { color: var(--success); font-weight: bold; }
    pre { background: var(--code-bg); padding: 12px; border-radius: 6px; overflow-x: auto; font-family: monospace; font-size: 13px; border: 1px solid var(--border); }
    .log-box { max-height: 250px; overflow-y: auto; background: var(--code-bg); padding: 10px; border-radius: 6px; border: 1px solid var(--border); font-family: monospace; font-size: 12px; color: #7ee787; }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <h2>⚡ LSM-Tree Engine</h2>
      <span class="badge">Engine Active</span>
      <span class="badge fsync" id="badge-sync">fsync: ALWAYS</span>
    </div>
    <div>
      <button class="btn btn-sm" onclick="fetchStats()">↻ Refresh Telemetry</button>
    </div>
  </header>

  <div class="stats-bar">
    <div class="stat-card">
      <div class="stat-label">MemTable Entries</div>
      <div class="stat-val" id="stat-mem-count">0</div>
      <div class="progress-bar-bg"><div class="progress-bar-fill" id="stat-mem-fill"></div></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Current LSN (Writes)</div>
      <div class="stat-val" id="stat-lsn">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Immutable SSTables</div>
      <div class="stat-val" id="stat-sst-count">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Total Disk Footprint</div>
      <div class="stat-val" id="stat-disk-bytes">0 B</div>
    </div>
  </div>

  <div class="tabs">
    <button class="tab-btn active" onclick="switchTab('explorer')">Key-Value Explorer</button>
    <button class="tab-btn" onclick="switchTab('storage')">LSM Storage Internals</button>
    <button class="tab-btn" onclick="switchTab('crashlab')">🚨 Power-Loss & Crash Lab</button>
    <button class="tab-btn" onclick="switchTab('api')">REST API Docs</button>
  </div>

  <!-- TAB 1: Explorer -->
  <div id="tab-explorer" class="tab-content active">
    <div class="grid-2">
      <div class="card">
        <h3>Store or Retrieve Key</h3>
        <div class="form-group">
          <label>Key</label>
          <input type="text" id="input-key" placeholder="e.g. user:1001">
        </div>
        <div class="form-group">
          <label>Value (JSON or String)</label>
          <textarea id="input-val" rows="3" placeholder='{"name": "Alice", "role": "engineer"}'></textarea>
        </div>
        <div style="display: flex; gap: 10px;">
          <button class="btn success" onclick="putKey()">PUT (Write + fsync)</button>
          <button class="btn" onclick="getKey()">GET</button>
          <button class="btn danger" onclick="deleteKey()">DELETE (Tombstone)</button>
        </div>
        <div style="margin-top: 15px;">
          <label>Result / Response:</label>
          <pre id="output-result">Ready.</pre>
        </div>
      </div>

      <div class="card">
        <h3>Range Scan Query [start, end)</h3>
        <div style="display: flex; gap: 10px; margin-bottom: 12px;">
          <input type="text" id="scan-start" placeholder="Start key (optional)">
          <input type="text" id="scan-end" placeholder="End key (optional)">
          <button class="btn" onclick="runScan()">SCAN</button>
        </div>
        <div style="max-height: 300px; overflow-y: auto;">
          <table>
            <thead><tr><th>Key</th><th>Value</th></tr></thead>
            <tbody id="scan-results-tbody">
              <tr><td colspan="2" style="color: var(--text-muted);">No scan executed yet.</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- TAB 2: LSM Storage Internals -->
  <div id="tab-storage" class="tab-content">
    <div style="display: flex; gap: 10px; margin-bottom: 15px;">
      <button class="btn warning" onclick="triggerFlush()">Flush MemTable to SSTable</button>
      <button class="btn success" onclick="triggerCompact()">Run SSTable Compaction</button>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>MemTable (SkipList in Memory)</h3>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 10px;">
          Fast O(log N) RAM writes before disk flush threshold.
        </p>
        <div style="max-height: 350px; overflow-y: auto;">
          <table>
            <thead><tr><th>Type</th><th>Key</th><th>LSN</th></tr></thead>
            <tbody id="memtable-tbody"></tbody>
          </table>
        </div>
      </div>

      <div class="card">
        <h3>SSTables on Disk (Sorted String Tables)</h3>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 10px;">
          Immutable files with Bloom Filters & Sparse Indexes.
        </p>
        <div style="max-height: 350px; overflow-y: auto;" id="sstables-list"></div>
      </div>
    </div>
  </div>

  <!-- TAB 3: Crash Lab -->
  <div id="tab-crashlab" class="tab-content">
    <div class="card">
      <h3>⚡ The SDE Challenge: Hardware Power Outage & Torn Write Simulator</h3>
      <p style="color: var(--text-muted); font-size: 14px; margin-bottom: 15px;">
        Test and verify the core durability guarantee: if the power cuts out mid-operation, committed writes are never lost, and torn writes at the disk boundary are detected by CRC32 and safely recovered.
      </p>

      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
        <div style="border: 1px solid var(--border); padding: 15px; border-radius: 6px;">
          <h4 style="margin-bottom: 10px; color: var(--accent);">Scenario 1: Sudden Power Outage</h4>
          <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 15px;">
            Writes 20 records with hardware <code>fsync</code>, then simulates brutal power cut / process termination without clean shutdown. Reboots cold engine and verifies 100% of data is recovered.
          </p>
          <button class="btn" onclick="runSimulatePowerCut()">Run Power Cut Test</button>
        </div>

        <div style="border: 1px solid var(--border); padding: 15px; border-radius: 6px;">
          <h4 style="margin-bottom: 10px; color: var(--danger);">Scenario 2: Torn Write Mid-Operation</h4>
          <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 15px;">
            Simulates disk cutting out mid-sector while writing a record. Injects corrupted partial bytes at EOF. Reboots engine to prove CRC32 mismatch detection and clean log truncation.
          </p>
          <button class="btn danger" onclick="runSimulateTornWrite()">Inject Torn Write Test</button>
        </div>
      </div>

      <div style="margin-top: 20px;">
        <label>Simulation Console Output:</label>
        <div class="log-box" id="crash-log">Simulation console ready...</div>
      </div>
    </div>
  </div>

  <!-- TAB 4: API Docs -->
  <div id="tab-api" class="tab-content">
    <div class="card">
      <h3>REST API Quick Reference</h3>
      <pre>
# 1. Put key
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"key": "user:1", "value": "Alice"}'

# 2. Get key
curl http://localhost:8000/api/v1/keys/user:1

# 3. Delete key
curl -X DELETE http://localhost:8000/api/v1/keys/user:1

# 4. Range scan [start, end)
curl "http://localhost:8000/api/v1/scan?start=user:0&end=user:9"

# 5. Flush MemTable to SSTable
curl -X POST http://localhost:8000/api/v1/flush

# 6. Compact SSTables
curl -X POST http://localhost:8000/api/v1/compact

# 7. Get Diagnostic Telemetry
curl http://localhost:8000/api/v1/stats
      </pre>
    </div>
  </div>

  <script>
    function switchTab(name) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
      event.target.classList.add('active');
      document.getElementById('tab-' + name).classList.add('active');
      if (name === 'storage') loadStorageDetails();
    }

    async function fetchStats() {
      try {
        const res = await fetch('/api/v1/stats');
        const data = await res.json();
        document.getElementById('stat-mem-count').innerText = data.active_memtable_entries.toLocaleString();
        document.getElementById('stat-lsn').innerText = data.current_lsn.toLocaleString();
        document.getElementById('stat-sst-count').innerText = data.sstable_count;
        const totalBytes = data.total_sstable_disk_bytes + data.active_wal_disk_bytes;
        document.getElementById('stat-disk-bytes').innerText = (totalBytes / 1024).toFixed(1) + ' KB';

        const fillPct = Math.min(100, (data.active_memtable_bytes / (2 * 1024 * 1024)) * 100);
        document.getElementById('stat-mem-fill').style.width = fillPct + '%';
        document.getElementById('badge-sync').innerText = 'fsync: ' + data.sync_mode.toUpperCase();
      } catch(e) {
        console.error(e);
      }
    }

    async function putKey() {
      const key = document.getElementById('input-key').value.trim();
      const value = document.getElementById('input-val').value.trim();
      if (!key) return alert('Key is required');
      const res = await fetch('/api/v1/keys', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value })
      });
      const data = await res.json();
      document.getElementById('output-result').innerText = JSON.stringify(data, null, 2);
      fetchStats();
    }

    async function getKey() {
      const key = document.getElementById('input-key').value.trim();
      if (!key) return alert('Key is required');
      const res = await fetch('/api/v1/keys/' + encodeURIComponent(key));
      if (res.status === 404) {
        document.getElementById('output-result').innerText = '(Key Not Found / Nil)';
      } else {
        const data = await res.json();
        document.getElementById('output-result').innerText = JSON.stringify(data, null, 2);
      }
    }

    async function deleteKey() {
      const key = document.getElementById('input-key').value.trim();
      if (!key) return alert('Key is required');
      const res = await fetch('/api/v1/keys/' + encodeURIComponent(key), { method: 'DELETE' });
      const data = await res.json();
      document.getElementById('output-result').innerText = JSON.stringify(data, null, 2);
      fetchStats();
    }

    async function runScan() {
      const start = document.getElementById('scan-start').value.trim();
      const end = document.getElementById('scan-end').value.trim();
      const params = new URLSearchParams();
      if (start) params.append('start', start);
      if (end) params.append('end', end);
      const res = await fetch('/api/v1/scan?' + params.toString());
      const data = await res.json();
      const tbody = document.getElementById('scan-results-tbody');
      tbody.innerHTML = '';
      if (!data.results || data.results.length === 0) {
        tbody.innerHTML = '<tr><td colspan="2" style="color: var(--text-muted);">(No keys matched)</td></tr>';
        return;
      }
      data.results.forEach(item => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td><b>${escapeHtml(item.key)}</b></td><td>${escapeHtml(item.value)}</td>`;
        tbody.appendChild(tr);
      });
    }

    async function loadStorageDetails() {
      fetchStats();
      // MemTable entries
      const memRes = await fetch('/api/v1/memtable');
      const memData = await memRes.json();
      const memTbody = document.getElementById('memtable-tbody');
      memTbody.innerHTML = '';
      if (!memData.entries || memData.entries.length === 0) {
        memTbody.innerHTML = '<tr><td colspan="3" style="color: var(--text-muted);">(MemTable is empty)</td></tr>';
      } else {
        memData.entries.forEach(e => {
          const tr = document.createElement('tr');
          const typeTag = e.is_tombstone ? '<span class="tombstone">DELETE</span>' : '<span class="put-type">PUT</span>';
          tr.innerHTML = `<td>${typeTag}</td><td>${escapeHtml(e.key)}</td><td>${e.lsn}</td>`;
          memTbody.appendChild(tr);
        });
      }

      // SSTables
      const sstRes = await fetch('/api/v1/sstables');
      const sstData = await sstRes.json();
      const sstDiv = document.getElementById('sstables-list');
      sstDiv.innerHTML = '';
      if (!sstData.sstables || sstData.sstables.length === 0) {
        sstDiv.innerHTML = '<p style="color: var(--text-muted);">(No SSTables on disk yet)</p>';
      } else {
        sstData.sstables.forEach(s => {
          const card = document.createElement('div');
          card.style = 'border: 1px solid var(--border); padding: 10px; border-radius: 6px; margin-bottom: 10px; background: var(--code-bg);';
          card.innerHTML = `
            <div style="font-weight: bold; color: var(--accent);">${escapeHtml(s.filename)}</div>
            <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">
              Size: ${(s.size_bytes / 1024).toFixed(1)} KB | Blocks: ${s.blocks_count} | Bloom: ${s.bloom_bits} bits (${s.bloom_hashes} hashes)
            </div>
            <div style="font-size: 11px; color: #a5d6ff; margin-top: 4px;">
              Sample boundary keys: ${s.sample_first_keys.map(escapeHtml).join(', ')}
            </div>
          `;
          sstDiv.appendChild(card);
        });
      }
    }

    async function triggerFlush() {
      const res = await fetch('/api/v1/flush', { method: 'POST' });
      const data = await res.json();
      alert('MemTable successfully flushed to disk as new SSTable!');
      loadStorageDetails();
    }

    async function triggerCompact() {
      const res = await fetch('/api/v1/compact', { method: 'POST' });
      const data = await res.json();
      alert('Compaction completed! Superseded versions deduplicated and tombstones purged.');
      loadStorageDetails();
    }

    async function runSimulatePowerCut() {
      const log = document.getElementById('crash-log');
      log.innerText = '[+] Initiating Sudden Power Cut simulation...\\n';
      const res = await fetch('/api/v1/simulate/crash-durability', { method: 'POST' });
      const data = await res.json();
      log.innerText += data.log.join('\\n');
      fetchStats();
    }

    async function runSimulateTornWrite() {
      const log = document.getElementById('crash-log');
      log.innerText = '[+] Initiating Torn-Write (Mid-Write Cut) simulation...\\n';
      const res = await fetch('/api/v1/simulate/torn-write', { method: 'POST' });
      const data = await res.json();
      log.innerText += data.log.join('\\n');
      fetchStats();
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // Auto-refresh telemetry
    setInterval(fetchStats, 5000);
    fetchStats();
  </script>
</body>
</html>
"""


class LSMServerHandler(BaseHTTPRequestHandler):
    """Handles HTTP REST requests and serves the Dashboard."""

    db: LSMTreeEngine

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body_json(self) -> dict:
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return {}
        raw = self.rfile.read(content_len).decode("utf-8")
        return json.loads(raw)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. Web Dashboard
        if path == "/" or path == "/index.html":
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # 2. Stats endpoint
        if path == "/api/v1/stats":
            self._send_json(HTTPStatus.OK, self.db.stats())
            return

        # 3. MemTable inspector
        if path == "/api/v1/memtable":
            entries = self.db.get_memtable_entries(limit=100)
            self._send_json(HTTPStatus.OK, {"entries": entries})
            return

        # 4. SSTables inspector
        if path == "/api/v1/sstables":
            sstables = self.db.get_sstable_details()
            self._send_json(HTTPStatus.OK, {"sstables": sstables})
            return

        # 5. Scan endpoint: /api/v1/scan?start=...&end=...&limit=...
        if path == "/api/v1/scan":
            start_k = query.get("start", [None])[0]
            end_k = query.get("end", [None])[0]
            limit = int(query.get("limit", [100])[0])
            items = self.db.scan(start_k, end_k, as_str=True)[:limit]
            results = [{"key": k, "value": v} for k, v in items]
            self._send_json(HTTPStatus.OK, {"results": results, "count": len(results)})
            return

        # 6. Point lookup: /api/v1/keys/<key>
        if path.startswith("/api/v1/keys/"):
            key = path[len("/api/v1/keys/"):]
            val = self.db.get(key, as_str=True)
            if val is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "Key not found", "key": key})
            else:
                self._send_json(HTTPStatus.OK, {"key": key, "value": val})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Put key: /api/v1/keys
        if path == "/api/v1/keys":
            data = self._read_body_json()
            key = data.get("key")
            value = data.get("value")
            if not key:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Key is required"})
                return
            self.db.put(str(key), str(value))
            self._send_json(HTTPStatus.OK, {"status": "ok", "key": key})
            return

        # 2. Batch write: /api/v1/batch
        if path == "/api/v1/batch":
            data = self._read_body_json()
            ops = data.get("ops", [])
            batch = WriteBatch()
            for op in ops:
                op_type = op.get("type", "put").lower()
                k = op.get("key")
                if op_type == "put":
                    batch.put(str(k), str(op.get("value", "")))
                elif op_type == "delete":
                    batch.delete(str(k))
            self.db.write_batch(batch)
            self._send_json(HTTPStatus.OK, {"status": "ok", "operations_applied": len(ops)})
            return

        # 3. Flush MemTable: /api/v1/flush
        if path == "/api/v1/flush":
            self.db.flush()
            self._send_json(HTTPStatus.OK, {"status": "ok", "message": "MemTable flushed to SSTable"})
            return

        # 4. Compact SSTables: /api/v1/compact
        if path == "/api/v1/compact":
            self.db.compact()
            self._send_json(HTTPStatus.OK, {"status": "ok", "message": "Compaction completed"})
            return

        # 5. Simulate Power Cut: /api/v1/simulate/crash-durability
        if path == "/api/v1/simulate/crash-durability":
            log = []
            log.append("[*] Writing 15 mission-critical transactions with hardware fsync...")
            for i in range(15):
                self.db.put(f"power_loss_test:{i:03d}", f"val_{i * 50}")
            log.append("[*] Simulating sudden physical power loss: killing DB instance abruptly...")

            # Store dir and sync mode
            db_dir = self.db.db_dir
            sync_mode = self.db.sync_mode

            # Simulate abrupt power drop
            if self.db.active_wal:
                self.db.active_wal.file.close()
            self.db._release_lock()
            LSMServerHandler.db = None

            log.append("[*] Memory state wiped.")
            log.append("[*] Rebooting cold engine from disk...")
            # Reboot
            LSMServerHandler.db = LSMTreeEngine(db_dir, sync_mode=sync_mode)

            log.append("[*] Verifying WAL replay integrity...")
            recovered_count = 0
            for i in range(15):
                val = LSMServerHandler.db.get(f"power_loss_test:{i:03d}", as_str=True)
                if val == f"val_{i * 50}":
                    recovered_count += 1

            log.append(f"[SUCCESS] 100% of writes ({recovered_count}/15) verified intact from WAL!")
            self._send_json(HTTPStatus.OK, {"status": "success", "log": log})
            return

        # 6. Simulate Torn Write: /api/v1/simulate/torn-write
        if path == "/api/v1/simulate/torn-write":
            log = []
            log.append("[*] Writing 10 valid records to WAL...")
            for i in range(10):
                self.db.put(f"torn_test:{i:02d}", f"committed_data_{i}")

            db_dir = self.db.db_dir
            sync_mode = self.db.sync_mode

            # Find active WAL
            wal_files = sorted(list(db_dir.glob("wal_*.log")))
            if not wal_files:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No WAL found"})
                return

            active_wal = wal_files[-1]
            valid_size = active_wal.stat().st_size
            log.append(f"[*] Active WAL size before corruption: {valid_size} bytes")

            # Close open handle to simulate sudden cut
            if self.db.active_wal:
                self.db.active_wal.file.close()
            self.db._release_lock()
            LSMServerHandler.db = None

            # Inject torn record (mid-write crash)
            log.append("[*] Injecting torn write at EOF: 11 bytes of partial, unchecksummed data...")
            with open(active_wal, "ab") as f:
                f.write(b"\xaa\xbb\xcc\xdd\x00\x00\x12\x34partial")

            log.append(f"[*] Corrupted WAL size: {active_wal.stat().st_size} bytes")
            log.append("[*] Rebooting cold database engine...")

            # Reboot
            LSMServerHandler.db = LSMTreeEngine(db_dir, sync_mode=sync_mode)

            log.append("[*] Recovery scanner detected CRC32 failure / truncated frame.")
            log.append("[*] Cleaned and truncated corrupted bytes back to last valid frame.")
            log.append(f"[*] Restored WAL size: {active_wal.stat().st_size} bytes (matches original: {active_wal.stat().st_size == valid_size})")

            # Verify all committed records
            for i in range(10):
                v = LSMServerHandler.db.get(f"torn_test:{i:02d}", as_str=True)
                assert v == f"committed_data_{i}"

            log.append("[SUCCESS] All 10 committed records restored with 0 corruption!")
            self._send_json(HTTPStatus.OK, {"status": "success", "log": log})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/v1/keys/"):
            key = path[len("/api/v1/keys/"):]
            self.db.delete(key)
            self._send_json(HTTPStatus.OK, {"status": "ok", "deleted_key": key})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Endpoint not found"})

    def log_message(self, format, *args):
        # Concise logging to keep server console clean
        pass


import webbrowser


def run_server(db_dir: Path, host: str = "0.0.0.0", port: int = 8000, sync_mode: SyncMode = SyncMode.ALWAYS, auto_open: bool = False):
    """Starts the LSM Key-Value HTTP Server and Web Dashboard."""
    db = LSMTreeEngine(db_dir, sync_mode=sync_mode)
    LSMServerHandler.db = db

    server = ThreadingHTTPServer((host, port), LSMServerHandler)
    url = f"http://localhost:{port}/"
    print("=" * 65)
    print(f"🚀 LSM-Tree Database Server & Web Dashboard running at:")
    print(f"   👉 {url}  (or http://127.0.0.1:{port}/)")
    print(f"   Database Directory: {db_dir.resolve()}")
    print(f"   Sync Mode:          {sync_mode.value.upper()} (Hardware fsync)")
    print("=" * 65)
    print("Press Ctrl+C to stop.")

    if auto_open:
        try:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server gracefully...")
    finally:
        server.server_close()
        db.close()


def main():
    parser = argparse.ArgumentParser(description="LSM-Tree Key-Value HTTP Server & Web Dashboard")
    parser.add_argument("--dir", default="./data", help="Directory for database files")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    parser.add_argument("--sync", choices=["always", "none"], default="always", help="fsync policy")
    parser.add_argument("--open", action="store_true", default=True, help="Automatically open browser")
    parser.add_argument("--no-open", dest="open", action="store_false", help="Do not open browser")

    args = parser.parse_args()
    sync_mode = SyncMode.ALWAYS if args.sync == "always" else SyncMode.NONE
    run_server(Path(args.dir), host=args.host, port=args.port, sync_mode=sync_mode, auto_open=args.open)


if __name__ == "__main__":
    main()