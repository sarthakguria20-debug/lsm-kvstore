# LSM-Tree Key-Value Storage Engine from Scratch

A crash-resilient, production-grade **Log-Structured Merge Tree (LSM-Tree)** key-value database engine implemented completely from scratch using standard Python.

Designed to address the **Senior Systems Engineer (SDE) Challenge**: **Guaranteeing absolute zero data loss and state consistency even if the physical power goes out in the middle of a write operation (Write-Ahead Logging + Hardware `fsync`).**

---

## 🌐 Interactive Website & Live Simulator

An interactive showcase website and engine simulator is hosted directly on GitHub Pages on the `main` branch:

👉 **[Launch Live Simulator & Architecture Explorer](https://sarthakguria20-debug.github.io/lsm-kvstore/)**

### Website Features

- **🎮 In-Browser LSM Engine Simulator**: Execute `PUT`, `GET`, `DELETE`, and `SCAN` operations in real time.
- **🔍 Step-by-Step Query Tracer**: Visualizes read-path traversal from MemTable to Bloom Filter fast rejects, Sparse Index binary search, and Data Block retrieval.
- **💥 Power Outage & Torn Write Simulator**: Simulate mid-write power loss, inject corrupted CRC32 frames, and execute automatic zero-data-loss recovery.
- **🧪 Kirsch-Mitzenmacher Bloom Filter Sandbox**: Explore double hashing, inspect flipped bit positions in a 64-bit array, and test membership.
- **💻 Interactive Terminal Shell**: Full in-browser CLI emulator with command history.

---

## Architecture Overview

```text
                        ┌──────────────────────────────────────────────┐
                        │                 Client API                   │
                        │        (PUT / GET / DELETE / SCAN)           │
                        └──────────────┬──────────────────▲────────────┘
                                       │                  │
                1. Write-Ahead Log     │                  │  4. Read Path
                   (WAL + CRC32)       │                  │
                                       ▼                  │
                        ┌──────────────────────────────┐  │
                        │       Write-Ahead Log        │  │
                        │       (fsync to disk)        │  │
                        └──────────────┬───────────────┘  │
                                       │                  │
                                       │ 2. Insert        │
                                       ▼                  │
                        ┌──────────────────────────────┐  │
                        │      Active MemTable         │──┼─── (1st read check)
                        │      (O(log N) SkipList)     │  │
                        └──────────────┬───────────────┘  │
                                       │                  │
                                       │ 3. Memory limit  │
                                       │    flushes to    │
                                       ▼                  │
                        ┌──────────────────────────────┐  │
                        │      Immutable SSTables      │──┘
                        │ ┌──────────────────────────┐ │      (2nd read check)
                        │ │  Bloom Filter (RAM)      │ │
                        │ ├──────────────────────────┤ │
                        │ │  Sparse Index (RAM)      │ │
                        │ ├──────────────────────────┤ │
                        │ │  Data Blocks (Disk)      │ │
                        │ └──────────────────────────┘ │
                        └──────────────┬───────────────┘
                                       │
                                       │ 5. Compaction
                                       ▼ (K-way Merge)
                        ┌──────────────────────────────┐
                        │   Consolidated SSTable       │
                        │ (Purges Old Keys & Tombs)    │
                        └──────────────────────────────┘
```

---

## The SDE Challenge: Durability & Power Outage Resilience

In distributed systems and database internals, the most critical reliability failure mode is a **sudden power cut / kernel panic during an in-flight write**.

### Why Standard File I/O Fails

Calling `file.write()` or even `file.flush()` in user-space code **does not write to disk platters or NAND flash chips**. It only transfers bytes from the application runtime buffer into the operating system kernel's page cache. If the machine loses power 50 milliseconds later, all uncommitted page cache data is permanently erased.

### How This Engine Solves It

1. **Hardware `fsync` Enforcement**:
   Every mutation (`PUT`, `DELETE`, or atomic `WriteBatch`) invokes `self.file.flush()` followed immediately by `os.fsync(self.fd)`. This forces the operating system kernel to issue a cache flush command to the physical storage controller before returning success to the user.

2. **Binary Frame Layout with CRC32 Checksums**:
   Every record written to the WAL has a binary frame:

    ```text
   +--------------------+-------------------------+-----------------------------------------+
   | CRC32 Checksum     | Payload Length          | Payload                                 |
   | (4 Bytes, Big-End) | (4 Bytes, Big-End)      | (Type, Timestamp, LSN, Key, Val)        |
   +--------------------+-------------------------+-----------------------------------------+
   ```

3. **Torn-Write Detection & Clean Recovery**:
   If power is cut *mid-write* (e.g. only 7 bytes of a 64-byte record reached disk before shutdown):
   - During boot recovery, `WALReader` parses every frame.
   - It computes `zlib.crc32(payload)` on disk bytes and compares it against the stored frame CRC32.
   - If a frame is incomplete, truncated at EOF, or has corrupted bits, the recovery engine detects the torn write, **restores all prior committed records**, and cleanly truncates the invalid trailing bytes (`os.ftruncate`) back to the last known durable boundary.

4. **Atomic Transactional Batches (`WriteBatch`)**:
   Multi-operation batches are bracketed by `TXN_BEGIN` and `TXN_COMMIT` markers. If a crash occurs before `TXN_COMMIT` is synced, the partial batch is automatically rolled back on recovery (ACID Atomicity).

5. **Atomic SSTable Flushes**:
   When flushing MemTables to SSTables, writes go to `.sst.tmp`. Only after `os.fsync` succeeds is `os.replace` invoked (an atomic filesystem operation on POSIX/macOS). Interrupted flushes leave clean temporary files that are safely pruned on reboot.

---

## Core Components

| Component | Implementation | Role |
| :--- | :--- | :--- |
| **Write-Ahead Log (WAL)** | [`lsm/wal.py`](lsm/wal.py) | Provides ACID Durability with CRC32 checksums, torn write detection, and recovery. |
| **MemTable** | [`lsm/memtable.py`](lsm/memtable.py) | In-memory mutable write buffer with memory tracking and immutability freezing. |
| **SkipList** | [`lsm/skiplist.py`](lsm/skiplist.py) | Probabilistic sorted map offering average `O(log N)` search, insert, and ordered iteration. |
| **Bloom Filter** | [`lsm/bloom.py`](lsm/bloom.py) | Kirsch-Mitzenmacher double-hashing filter rejecting ~99% of negative queries with zero disk I/O. |
| **SSTable** | [`lsm/sstable.py`](lsm/sstable.py) | Immutable on-disk sorted table with 4KB Data Blocks, Sparse Index, and Bloom Filter. |
| **Compactor** | [`lsm/compaction.py`](lsm/compaction.py) | K-way merge sort deduplicating superseded keys and reclaiming disk space from tombstones. |
| **Engine** | [`lsm/engine.py`](lsm/engine.py) | Top-level coordinator managing locks, MemTables, WAL rotation, multi-level reads, and scans. |
| **CLI & REPL** | [`lsm/cli.py`](lsm/cli.py) | Command-line tool and interactive terminal shell for inspecting and operating the database. |

---

## Benchmark Results (Apple Silicon M-Series)

Running `python3 benchmark.py`:

```text
=================================================================
  LSM-TREE KEY-VALUE ENGINE: PERFORMANCE BENCHMARK
=================================================================

[1] Sequential Writes (10,000 entries, SyncMode=NONE):
    Throughput:     152,039 ops/sec
    Avg Latency:    6.6 µs/op

[2] Point Read Lookups (10,000 existing keys):
    Read Throughput:18,155 ops/sec
    Avg Latency:    55.1 µs/op

[3] Negative Lookups (5,000 non-existent keys, Bloom filter test):
    Filter Speed:   410,949 ops/sec (zero disk reads)
    Avg Latency:    2.4 µs/op

[4] Range Scan Queries (100 range scans across MemTable & SSTables):
    Scan Latency:   0.19 ms/scan

[5] Compaction & Space Reclamation:
    Pre-compaction SSTable count: 4 (1,141,197 bytes)
    Compaction Time:              0.050 s
    Post-compaction SSTable count:1 (824,929 bytes)
    Reclaimed Space:              316,268 bytes (27.7% reduction)

[6] Strict Hardware Durability (500 writes with os.fsync() on every op):
    Durability IOPS:40,299 physical syncs/sec
    Disk Sync Cost: 0.02 ms/fsync
=================================================================
```

---

## Quickstart & Usage

### 1. Python API

```python
from lsm.engine import LSMTreeEngine, WriteBatch
from lsm.wal import SyncMode

# Open or create database
with LSMTreeEngine("./my_database", sync_mode=SyncMode.ALWAYS) as db:
    # 1. Put
    db.put("user:101", '{"name": "Alice", "role": "engineer"}')
    db.put("user:102", '{"name": "Bob", "role": "architect"}')

    # 2. Get
    user = db.get("user:101", as_str=True)
    print(user)

    # 3. Delete (writes tombstone record)
    db.delete("user:102")

    # 4. Atomic Batch Transaction
    batch = WriteBatch()
    batch.put("config:theme", "dark")
    batch.put("config:lang", "en")
    batch.delete("config:legacy_flag")
    db.write_batch(batch)

    # 5. Range Scan [start_key, end_key)
    for key, val in db.scan("config:", "config:\xff", as_str=True):
        print(f"{key} => {val}")

    # 6. Maintenance
    db.flush()    # Force MemTable flush to SSTable
    db.compact()  # Merge SSTables and reclaim space
```

### 2. Standalone Web Dashboard & REST Server Application

Launch the full-stack database application:

```bash
python3 -m lsm.server --port 8000 --dir ./data
```

Visit **`http://localhost:8000`** in your browser to access:

- **Live LSM Storage Visualizer**: Real-time view of the active MemTable (SkipList entries in RAM), on-disk SSTables with Bloom Filter and block metadata.
- **Key-Value Explorer**: Interactive PUT, GET, DELETE, and Range Scan form.
- **Power-Outage & Crash Lab**: Interactive buttons to simulate hard power cuts and mid-write torn sectors with live recovery verification.
- **One-Click Compaction**: Watch fragmented SSTables merge into a unified file and see reclaimed disk space immediately.

#### REST API Endpoints

```bash
# 1. Put Key
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"key": "user:1", "value": "Alice"}'

# 2. Get Key
curl http://localhost:8000/api/v1/keys/user:1

# 3. Range Scan [start, end)
curl "http://localhost:8000/api/v1/scan?start=user:0&end=user:9"

# 4. Trigger Compaction
curl -X POST http://localhost:8000/api/v1/compact

# 5. Engine Telemetry
curl http://localhost:8000/api/v1/stats
```

---

### 3. Financial Ledger & Audit Application: Crash-Resilient Transfers (`examples/ledger_app.py`)

A real-world financial system built on the engine demonstrating ACID durability, double-entry bookkeeping, and crash resilience:

```bash
python3 examples/ledger_app.py
```

Output:

```text
=================================================================
  FINANCIAL LEDGER APPLICATION (LSM-Tree Powered)
=================================================================

[1] Creating Accounts...
    Account alice      (Alice Johnson  ): $1000.00
    Account bob        (Bob Smith      ): $250.00
    Account charlie    (Charlie Brown  ): $50.00

[2] Executing Atomic Transfers...
    Transfer 1: tx_3c7b8142 | Alice -> Bob $300.00 | Status: COMMITTED (fsync)
    Transfer 2: tx_faa2806b | Bob -> Charlie $75.50 | Status: COMMITTED (fsync)

[3] Account Balances After Transfers:
    Account alice      (Alice Johnson  ): $700.00
    Account bob        (Bob Smith      ): $474.50
    Account charlie    (Charlie Brown  ): $125.50

[4] Chronological Audit Trail for 'bob':
    CREDIT  $300.00    with alice
    DEBIT   $75.50     with charlie

[5] Demonstrating Crash Resilience (Sudden Power Loss Simulation):
    Abruptly resetting storage engine to emulate power outage...
    Reboot complete. Verifying balances and audit integrity:
    Account alice      (Alice Johnson  ): $700.00 (100% durable)
    Account bob        (Bob Smith      ): $474.50 (100% durable)
    Account charlie    (Charlie Brown  ): $125.50 (100% durable)
=================================================================
```

---

### 4. Interactive Terminal REPL & CLI Demo

Launch the full-stack database application:

```bash
python3 -m lsm.server --port 8000 --dir ./data
```

Visit **`http://localhost:8000`** in your browser to access:

- **Live LSM Storage Visualizer**: Real-time view of the active MemTable (SkipList entries in RAM), on-disk SSTables with Bloom Filter and block metadata.
- **Key-Value Explorer**: Interactive PUT, GET, DELETE, and Range Scan form.
- **Power-Outage & Crash Lab**: Interactive buttons to simulate hard power cuts and mid-write torn sectors with live recovery verification.
- **One-Click Compaction**: Watch fragmented SSTables merge into a unified file and see reclaimed disk space immediately.

#### REST API Endpoints

```bash
# 1. Put Key
curl -X POST http://localhost:8000/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"key": "user:1", "value": "Alice"}'

# 2. Get Key
curl http://localhost:8000/api/v1/keys/user:1

# 3. Range Scan [start, end)
curl "http://localhost:8000/api/v1/scan?start=user:0&end=user:9"

# 4. Trigger Compaction
curl -X POST http://localhost:8000/api/v1/compact

# 5. Engine Telemetry
curl http://localhost:8000/api/v1/stats
```

---

### 3. Financial Ledger & Audit Application (`examples/ledger_app.py`)

A real-world financial system built on the engine demonstrating ACID durability, double-entry bookkeeping, and crash resilience:

```bash
python3 examples/ledger_app.py
```

Output:

```text
=================================================================
  FINANCIAL LEDGER APPLICATION (LSM-Tree Powered)
=================================================================

[1] Creating Accounts...
    Account alice      (Alice Johnson  ): $1000.00
    Account bob        (Bob Smith      ): $250.00
    Account charlie    (Charlie Brown  ): $50.00

[2] Executing Atomic Transfers...
    Transfer 1: tx_3c7b8142 | Alice -> Bob $300.00 | Status: COMMITTED (fsync)
    Transfer 2: tx_faa2806b | Bob -> Charlie $75.50 | Status: COMMITTED (fsync)

[3] Account Balances After Transfers:
    Account alice      (Alice Johnson  ): $700.00
    Account bob        (Bob Smith      ): $474.50
    Account charlie    (Charlie Brown  ): $125.50

[4] Chronological Audit Trail for 'bob':
    CREDIT  $300.00    with alice
    DEBIT   $75.50     with charlie

[5] Demonstrating Crash Resilience (Sudden Power Loss Simulation):
    Abruptly resetting storage engine to emulate power outage...
    Reboot complete. Verifying balances and audit integrity:
    Account alice      (Alice Johnson  ): $700.00 (100% durable)
    Account bob        (Bob Smith      ): $474.50 (100% durable)
    Account charlie    (Charlie Brown  ): $125.50 (100% durable)
=================================================================
```

---

### 4. Interactive Terminal REPL & CLI

Launch the interactive prompt:

```bash
python3 -m lsm.cli --dir ./demo_db repl
```

```text
============================================================
 Welcome to LSM-Tree Key-Value Engine REPL
 Commands:
   SET <key> <value>   - Store key-value pair
   GET <key>           - Retrieve value by key
   DEL <key>           - Delete key (tombstone)
   SCAN [start] [end]  - Range scan keys
   FLUSH               - Flush MemTable to SSTable
   COMPACT             - Run SSTable merge compaction
   STATS               - Show engine statistics
   EXIT / QUIT         - Exit REPL
============================================================
lsm-kv> SET server:status active
OK
lsm-kv> GET server:status
"active"
lsm-kv> EXIT
```

### 3. Command-Line CLI

```bash
# Write key
python3 -m lsm.cli --dir ./demo_db put session:token "xyz987"

# Read key
python3 -m lsm.cli --dir ./demo_db get session:token

# Scan keys
python3 -m lsm.cli --dir ./demo_db scan session: session:\xff

# Inspect statistics
python3 -m lsm.cli --dir ./demo_db stats
```

---

## Test Suite & Verification

The test suite covers full unit, integration, and failure-injection tests:

```bash
python3 -m unittest discover -s tests -v
```

### Failure Injection & Crash Tests ([`tests/test_crash_safety.py`](tests/test_crash_safety.py))

- `test_power_loss_after_fsync_guarantees_durability`: Simulates immediate power loss after write acknowledgment; verifies 100% data recovery from WAL.
- `test_power_loss_during_write_torn_write_recovery`: Injects torn/truncated bytes mid-record at EOF; verifies CRC mismatch detection, clean log truncation, and zero data loss for preceding commits.
- `test_crash_during_sstable_flush`: Simulates system crash during MemTable flush leaving orphaned `.tmp` files; verifies auto-pruning and consistent recovery.
- `test_hard_sigkill_subprocess_simulation`: Spawns a background subprocess writing records, terminates it with real `kill -9` (`SIGKILL`), and proves recovery in the parent process.
- `test_atomic_transaction_abort_on_crash`: Simulates crash before `TXN_COMMIT`; verifies atomicity rollback.
