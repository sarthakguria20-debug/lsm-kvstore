"""
Unit tests for SSTable (Sorted String Table):
Block writing, sparse index seeking, Bloom filter integration, and range queries.
"""

import tempfile
import unittest
from pathlib import Path
from lsm.sstable import SSTableReader, SSTableWriter
from lsm.types import Record, RecordType


class TestSSTable(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sst_path = Path(self.temp_dir.name) / "sstable_000001.sst"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_write_and_read_records(self):
        records = [
            Record(f"k_{i:04d}".encode("utf-8"), f"v_{i}".encode("utf-8"), RecordType.PUT, i, i)
            for i in range(100)
        ]

        # Use small block size to force creation of multiple blocks and sparse index entries
        writer = SSTableWriter(self.sst_path, block_size=256, expected_keys=100)
        written_count = writer.write_records(records)
        self.assertEqual(written_count, 100)

        # File should exist, .tmp should not exist
        self.assertTrue(self.sst_path.exists())
        self.assertFalse(Path(str(self.sst_path) + ".tmp").exists())

        reader = SSTableReader(self.sst_path)
        self.assertGreater(len(reader.sparse_index), 1)

        # Test point lookups
        rec = reader.get(b"k_0042")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.value, b"v_42")

        # Test missing key (Bloom filter / sparse index rejection)
        self.assertIsNone(reader.get(b"k_9999"))

    def test_range_scan_across_blocks(self):
        records = [
            Record(f"key_{i:03d}".encode("utf-8"), f"val_{i}".encode("utf-8"), RecordType.PUT, i, i)
            for i in range(50)
        ]
        writer = SSTableWriter(self.sst_path, block_size=128, expected_keys=50)
        writer.write_records(records)

        reader = SSTableReader(self.sst_path)
        scanned = list(reader.range_scan(b"key_010", b"key_015"))
        self.assertEqual(len(scanned), 5)
        self.assertEqual([r.key for r in scanned], [
            b"key_010", b"key_011", b"key_012", b"key_013", b"key_014"
        ])


if __name__ == "__main__":
    unittest.main()

