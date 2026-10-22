"""
Power Outage & Crash Resilience Test Suite (The SDE Challenge).
Simulates sudden power outages, process SIGKILL, mid-write torn sectors,
corrupted WAL frames, and interrupted SSTable flushes.
"""

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lsm.engine import LSMTreeEngine, WriteBatch # type: ignore
from lsm.wal import SyncMode # pyright: ignore[reportMissingImports]


class TestCrashSafety(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_power_loss_after_fsync_guarantees_durability(self):
        """
        Scenario 1: Power goes out right after writes are acknowledged.
        Engine was never gracefully closed (simulated power off).
        On reboot, WAL replay must restore 100% of the committed data into MemTable.
        """
        # Open DB, write records, but DO NOT call close() or flush()
        db = LSMTreeEngine(self.db_dir, sync_mode=SyncMode.ALWAYS)
        for i in range(100):
            db.put(f"order:{i:03d}", f"item_details_{i}")

        # Simulate sudden power cut: OS kills process and closes open file descriptors
        if db.active_wal:
            db.active_wal.file.close()
        db._release_lock()
        del db  # memory wiped!

        # Reboot engine from cold disk
        rebooted_db = LSMTreeEngine(self.db_dir)
        try:
            for i in range(100):
                val = rebooted_db.get(f"order:{i:03d}", as_str=True)
                self.assertEqual(val, f"item_details_{i}")
        finally:
            rebooted_db.close()

    def test_power_loss_during_write_torn_write_recovery(self):
        """
        Scenario 2: Power cuts out MID-WRITE while a record is physically being written to disk.
        Disk has torn/truncated bytes at EOF.
        The recovery engine must detect the broken CRC32 / partial frame,
        truncate the partial record, preserve all preceding committed records,
        and remain fully operational for subsequent writes.
        """
        db = LSMTreeEngine(self.db_dir, sync_mode=SyncMode.ALWAYS)
        for i in range(20):
            db.put(f"tx:{i:02d}", f"amount_{i * 100}")
        if db.active_wal:
            db.active_wal.file.close()
        db._release_lock()
        del db

        # Find the active WAL file
        wal_files = list(self.db_dir.glob("wal_*.log"))
        self.assertTrue(len(wal_files) > 0)
        active_wal = wal_files[0]

        valid_size = active_wal.stat().st_size

        # Simulate torn write: write incomplete header and partial garbage payload
        with open(active_wal, "ab") as f:
            f.write(b"\xde\xad\xbe\xef\x00\x00\x00\x30partial_torn_data")

        torn_size = active_wal.stat().st_size
        self.assertGreater(torn_size, valid_size)

        # Reboot engine
        rebooted_db = LSMTreeEngine(self.db_dir)
        try:
            # 1. All 20 committed records must be perfectly recovered
            for i in range(20):
                val = rebooted_db.get(f"tx:{i:02d}", as_str=True)
                self.assertEqual(val, f"amount_{i * 100}")

            # 2. Torn write must be truncated from WAL
            self.assertEqual(active_wal.stat().st_size, valid_size)

            # 3. New writes after recovery must succeed seamlessly
            rebooted_db.put("tx:20", "amount_2000")
            self.assertEqual(rebooted_db.get("tx:20", as_str=True), "amount_2000")
        finally:
            rebooted_db.close()

    def test_crash_during_sstable_flush(self):
        """
        Scenario 3: System crashes while flushing MemTable to SSTable.
        An orphaned .sst.tmp file exists on disk.
        On reboot, the engine must safely clean up the orphaned .tmp file
        and restore state from the durable WAL.
        """
        db = LSMTreeEngine(self.db_dir, sync_mode=SyncMode.ALWAYS)
        db.put("key_a", "val_a")
        db.put("key_b", "val_b")

        # Simulate half-written SSTable temporary file
        orphan_tmp = self.db_dir / "sstable_000099.sst.tmp"
        orphan_tmp.write_bytes(b"INCOMPLETE_SSTABLE_DATA_CORRUPTED_MID_FLUSH")

        if db.active_wal:
            db.active_wal.file.close()
        db._release_lock()
        del db

        self.assertTrue(orphan_tmp.exists())

        # Reboot
        rebooted_db = LSMTreeEngine(self.db_dir)
        try:
            # Orphaned .tmp file should have been cleaned up automatically
            self.assertFalse(orphan_tmp.exists())
            self.assertEqual(rebooted_db.get("key_a", as_str=True), "val_a")
            self.assertEqual(rebooted_db.get("key_b", as_str=True), "val_b")
        finally:
            rebooted_db.close()

    def test_hard_sigkill_subprocess_simulation(self):
        """
        Scenario 4: External process is terminated with SIGKILL (-9) mid-operation.
        Verifies real operating system process crash recovery.
        """
        script = f"""
import sys, os
from pathlib import Path
sys.path.insert(0, '{str(Path(__file__).parent.parent.resolve())}')
from lsm.engine import LSMTreeEngine
from lsm.wal import SyncMode

db = LSMTreeEngine('{str(self.db_dir)}', sync_mode=SyncMode.ALWAYS)
for i in range(50):
    db.put(f"sigkill_key_{{i:02d}}", f"sigkill_val_{{i}}")

# Tell parent process that writes are synced
print("READY", flush=True)

# Loop forever until parent kills with SIGKILL
import time
while True:
    time.sleep(1)
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait until child process reports READY
            line = proc.stdout.readline().strip() # type: ignore
            self.assertEqual(line, "READY")

            # Brutally terminate child process with SIGKILL (power cut emulation)
            proc.kill()
            proc.wait()
        finally:
            proc.stdout.close() # type: ignore
            assert proc.stderr is not None
            proc.stderr.close()

        # Open database from parent and verify durability
        with LSMTreeEngine(self.db_dir) as db:
            for i in range(50):
                val = db.get(f"sigkill_key_{i:02d}", as_str=True)
                self.assertEqual(val, f"sigkill_val_{i}")


if __name__ == "__main__":
    unittest.main()
