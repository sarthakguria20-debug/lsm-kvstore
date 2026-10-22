"""
Unit tests for Write-Ahead Logging (WAL):
Durability, CRC32 checksum verification, torn write recovery, and atomic transactions.
"""

import os
import struct
import tempfile
import unittest
from pathlib import Path

from lsm.types import Record, RecordType
from lsm.wal import SyncMode, WALReader, WALWriter, HEADER_FORMAT, PAYLOAD_META_FORMAT


class TestWAL(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.wal_path = Path(self.temp_dir.name) / "test_wal.log"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_basic_append_and_recover(self):
        writer = WALWriter(self.wal_path, sync_mode=SyncMode.ALWAYS)
        writer.append_record(RecordType.PUT, b"user:101", b"Alice", lsn=1)
        writer.append_record(RecordType.PUT, b"user:102", b"Bob", lsn=2)
        writer.append_record(RecordType.DELETE, b"user:101", None, lsn=3)
        writer.close()

        reader = WALReader(self.wal_path)
        records, max_lsn, had_corruption = reader.recover()

        self.assertFalse(had_corruption)
        self.assertEqual(max_lsn, 3)
        self.assertEqual(len(records), 3)

        self.assertEqual(records[0].key, b"user:101")
        self.assertEqual(records[0].value, b"Alice")
        self.assertEqual(records[0].record_type, RecordType.PUT)

        self.assertEqual(records[1].key, b"user:102")
        self.assertEqual(records[1].value, b"Bob")

        self.assertEqual(records[2].key, b"user:101")
        self.assertEqual(records[2].record_type, RecordType.DELETE)

    def test_torn_write_recovery_at_eof(self):
        """Simulate power cutting out while the OS was writing bytes to the disk."""
        writer = WALWriter(self.wal_path, sync_mode=SyncMode.ALWAYS)
        writer.append_record(RecordType.PUT, b"key1", b"val1", lsn=1)
        writer.append_record(RecordType.PUT, b"key2", b"val2", lsn=2)
        writer.close()

        valid_size = self.wal_path.stat().st_size

        # Incomplete / torn write: append 7 bytes of a 30-byte record (power cut mid-write)
        with open(self.wal_path, "ab") as f:
            f.write(b"\x12\x34\x56\x78\x00\x00\x1f")

        corrupted_size = self.wal_path.stat().st_size
        self.assertGreater(corrupted_size, valid_size)

        # Recovery should detect torn write, recover preceding records, and truncate the garbage
        reader = WALReader(self.wal_path)
        records, max_lsn, had_corruption = reader.recover(truncate_torn_records=True)

        self.assertTrue(had_corruption)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].key, b"key1")
        self.assertEqual(records[1].key, b"key2")
        self.assertEqual(max_lsn, 2)

        # Ensure file was truncated back to clean valid state
        self.assertEqual(self.wal_path.stat().st_size, valid_size)

    def test_corrupted_crc32_detection(self):
        """Simulate bit rot or sector corruption where payload bytes are modified."""
        writer = WALWriter(self.wal_path, sync_mode=SyncMode.ALWAYS)
        writer.append_record(RecordType.PUT, b"alpha", b"first", lsn=1)
        writer.append_record(RecordType.PUT, b"beta", b"second", lsn=2)
        writer.close()

        # Corrupt the payload of the second record
        with open(self.wal_path, "r+b") as f:
            f.seek(-3, os.SEEK_END)
            f.write(b"XYZ")  # overwrite value bytes without updating CRC

        reader = WALReader(self.wal_path)
        records, max_lsn, had_corruption = reader.recover(truncate_torn_records=True)

        self.assertTrue(had_corruption)
        # The first record must still be intact!
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].key, b"alpha")

    def test_atomic_transaction_abort_on_crash(self):
        """Simulate system crash before TXN_COMMIT was written to disk."""
        writer = WALWriter(self.wal_path, sync_mode=SyncMode.ALWAYS)
        # Completed single write
        writer.append_record(RecordType.PUT, b"k0", b"v0", lsn=1)

        # Start a batch transaction
        r1 = Record(b"batch_k1", b"v1", RecordType.PUT, 100, lsn=2)
        r2 = Record(b"batch_k2", b"v2", RecordType.PUT, 101, lsn=3)
        # Append without commit to simulate crash before TXN_COMMIT
        writer.append_batch([r1, r2], auto_commit=False)
        writer.close()

        reader = WALReader(self.wal_path)
        records, max_lsn, had_corruption = reader.recover()

        self.assertTrue(had_corruption)
        # Only the committed k0 record should exist; uncommitted transaction was rolled back!
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].key, b"k0")


if __name__ == "__main__":
    unittest.main()

