"""
Unit tests for BloomFilter:
100% recall (zero false negatives), empirical false positive rate validation,
and binary serialization/deserialization.
"""

import unittest
from lsm.bloom import BloomFilter


class TestBloomFilter(unittest.TestCase):

    def test_zero_false_negatives(self):
        bf = BloomFilter(expected_elements=500, false_positive_rate=0.01)
        added_keys = [f"key_{i}".encode("utf-8") for i in range(500)]

        for k in added_keys:
            bf.add(k)

        # Every inserted key MUST return True (no false negatives allowed in Bloom filter)
        for k in added_keys:
            self.assertTrue(bf.contains(k))

    def test_false_positive_rate(self):
        target_fp_rate = 0.05
        bf = BloomFilter(expected_elements=1000, false_positive_rate=target_fp_rate)

        for i in range(1000):
            bf.add(f"present_{i}".encode("utf-8"))

        # Test with 2000 keys that were never inserted
        false_positives = 0
        test_samples = 2000
        for i in range(test_samples):
            if bf.contains(f"absent_{i}".encode("utf-8")):
                false_positives += 1

        empirical_fp_rate = false_positives / test_samples
        # Empirical rate should be roughly close to target (allow slack <= 2.5x target)
        self.assertLess(empirical_fp_rate, target_fp_rate * 2.5)

    def test_serialization_roundtrip(self):
        bf = BloomFilter(expected_elements=200, false_positive_rate=0.02)
        test_keys = [b"foo", b"bar", b"baz", b"qux"]
        for k in test_keys:
            bf.add(k)

        serialized = bf.serialize()
        restored = BloomFilter.deserialize(serialized)

        for k in test_keys:
            self.assertTrue(restored.contains(k))

        self.assertFalse(restored.contains(b"unknown_key_xyz"))


if __name__ == "__main__":
    unittest.main()

