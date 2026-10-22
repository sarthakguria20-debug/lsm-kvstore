"""
Unit tests for SSTable Compaction:
k-way merge, deduplication of superseded versions, and tombstone purging.
"""

import tempfile
import unittest
from pathlib import Path

from lsm.engine import LSMTreeEngine # type: ignore
from lsm.wal import SyncMode # type: ignore


class TestCompaction(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compaction_deduplication_and_tombstone_purging(self):
        # Use large max_memtable_size_bytes so flushes only happen when explicitly requested
        with LSMTreeEngine(self.db_dir, max_memtable_size_bytes=1024 * 1024) as db:
            # Batch 1: write keys 1..20 and force flush
            for i in range(1, 21):
                db.put(f"key_{i:02d}", f"v1_{i}")
            db.flush()

            # Batch 2: overwrite keys 1..10 with new values and force flush
            for i in range(1, 11):
                db.put(f"key_{i:02d}", f"v2_updated_{i}")
            db.flush()

            # Batch 3: delete keys 15..20 and force flush
            for i in range(15, 21):
                db.delete(f"key_{i:02d}")
            db.flush()

            # We now have exactly 3 SSTables
            self.assertEqual(len(db.sstable_readers), 3)

            # Trigger compaction
            db.compact()

            # After full compaction, all SSTables should be merged into a single consolidated SSTable
            self.assertEqual(len(db.sstable_readers), 1)

            # Verify values:
            # 1..10 should have updated v2 values
            for i in range(1, 11):
                self.assertEqual(db.get(f"key_{i:02d}", as_str=True), f"v2_updated_{i}")

            # 11..14 should have original v1 values
            for i in range(11, 15):
                self.assertEqual(db.get(f"key_{i:02d}", as_str=True), f"v1_{i}")

            # 15..20 should be absent (purged)
            for i in range(15, 21):
                self.assertIsNone(db.get(f"key_{i:02d}"))

            # Scan should only contain exactly 14 active keys (1..14)
            items = db.scan(as_str=True)
            self.assertEqual(len(items), 14)


if __name__ == "__main__":
    unittest.main()
