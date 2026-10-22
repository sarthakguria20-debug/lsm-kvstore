"""
Unit tests for SkipList in-memory data structure.
"""

import unittest
from lsm.skiplist import SkipList
from lsm.types import Record, RecordType


class TestSkipList(unittest.TestCase):

    def test_insert_and_lookup(self):
        sl = SkipList()
        r1 = Record(b"apple", b"red", RecordType.PUT, 1)
        r2 = Record(b"banana", b"yellow", RecordType.PUT, 2)
        r3 = Record(b"cherry", b"dark_red", RecordType.PUT, 3)

        sl.put(r1)
        sl.put(r2)
        sl.put(r3)

        self.assertEqual(sl.count, 3)
        self.assertEqual(sl.get(b"banana").value, b"yellow")
        self.assertIsNone(sl.get(b"orange"))

    def test_overwrite_existing_key(self):
        sl = SkipList()
        r1 = Record(b"counter", b"1", RecordType.PUT, 1)
        r2 = Record(b"counter", b"2", RecordType.PUT, 2)

        sl.put(r1)
        self.assertEqual(sl.get(b"counter").value, b"1")

        sl.put(r2)
        self.assertEqual(sl.get(b"counter").value, b"2")
        self.assertEqual(sl.count, 1)

    def test_lexicographical_iteration(self):
        sl = SkipList()
        keys = [b"delta", b"alpha", b"charlie", b"bravo", b"echo"]
        for k in keys:
            sl.put(Record(k, k.upper(), RecordType.PUT, 1))

        sorted_keys = [rec.key for rec in sl]
        self.assertEqual(sorted_keys, [b"alpha", b"bravo", b"charlie", b"delta", b"echo"])

    def test_range_scan(self):
        sl = SkipList()
        for i in range(10):
            k = f"key_{i:02d}".encode("utf-8")
            sl.put(Record(k, f"val_{i}".encode("utf-8"), RecordType.PUT, i))

        # Scan range [key_03, key_07)
        scanned = [rec.key for rec in sl.range_scan(b"key_03", b"key_07")]
        expected = [b"key_03", b"key_04", b"key_05", b"key_06"]
        self.assertEqual(scanned, expected)


if __name__ == "__main__":
    unittest.main()

