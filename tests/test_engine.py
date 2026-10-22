"""
Integration tests for the LSM-Tree Key-Value Engine.
"""

import tempfile
import unittest
from pathlib import Path

from lsm.engine import LSMTreeEngine, WriteBatch
from lsm.wal import SyncMode


class TestLSMTreeEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_basic_crud_operations(self):
        with LSMTreeEngine(self.db_dir, sync_mode=SyncMode.ALWAYS) as db:
            db.put("user:1", "Alice")
            db.put("user:2", "Bob")

            self.assertEqual(db.get("user:1", as_str=True), "Alice")
            self.assertEqual(db.get("user:2", as_str=True), "Bob")
            self.assertIsNone(db.get("user:3"))

            # Overwrite
            db.put("user:1", "Alicia")
            self.assertEqual(db.get("user:1", as_str=True), "Alicia")

            # Delete
            db.delete("user:2")
            self.assertIsNone(db.get("user:2"))

    def test_flush_and_sstable_read(self):
        # Set low memtable threshold to force automatic flushes to SSTables
        with LSMTreeEngine(self.db_dir, max_memtable_size_bytes=512) as db:
            for i in range(50):
                db.put(f"key_{i:03d}", f"value_payload_{i}")

            # Verify that flushes occurred and SSTables were created
            stats = db.stats()
            self.assertGreater(stats["sstable_count"], 0)

            # All keys must still be completely retrievable across SSTables
            for i in range(50):
                val = db.get(f"key_{i:03d}", as_str=True)
                self.assertEqual(val, f"value_payload_{i}")

    def test_range_scan_across_memtable_and_sstables(self):
        with LSMTreeEngine(self.db_dir, max_memtable_size_bytes=512) as db:
            # Write 0..29 and flush to SSTable
            for i in range(30):
                db.put(f"item_{i:02d}", f"v_{i}")
            db.flush()

            # Write 30..49 in MemTable
            for i in range(30, 50):
                db.put(f"item_{i:02d}", f"v_{i}")

            # Delete item_15 and item_35
            db.delete("item_15")
            db.delete("item_35")

            # Scan items in range [item_10, item_25)
            results = db.scan("item_10", "item_25", as_str=True)
            keys = [k for k, _ in results]

            self.assertIn("item_10", keys)
            self.assertIn("item_14", keys)
            self.assertNotIn("item_15", keys)  # deleted by tombstone!
            self.assertIn("item_16", keys)
            self.assertNotIn("item_25", keys)  # exclusive upper bound

    def test_write_batch_atomicity(self):
        with LSMTreeEngine(self.db_dir) as db:
            batch = WriteBatch()
            batch.put("batch:1", "first")
            batch.put("batch:2", "second")
            batch.delete("batch:1")
            batch.put("batch:3", "third")

            db.write_batch(batch)

            self.assertIsNone(db.get("batch:1"))
            self.assertEqual(db.get("batch:2", as_str=True), "second")
            self.assertEqual(db.get("batch:3", as_str=True), "third")


if __name__ == "__main__":
    unittest.main()

