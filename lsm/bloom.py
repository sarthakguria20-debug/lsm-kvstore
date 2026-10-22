"""
Space-efficient Bloom Filter for rapid negative key lookups.
Avoids costly disk I/O when a key does not exist in an SSTable.
"""

from __future__ import annotations
import math
import struct
import hashlib
from typing import Union


class BloomFilter:
    """
    Bloom filter using Kirsch-Mitzenmacher double hashing technique:
    g_i(x) = (h1(x) + i * h2(x)) % m
    """

    def __init__(self, expected_elements: int = 1000, false_positive_rate: float = 0.01):
        if expected_elements <= 0:
            expected_elements = 1
        if not (0 < false_positive_rate < 1):
            false_positive_rate = 0.01

        self.expected_elements = expected_elements
        self.false_positive_rate = false_positive_rate

        # Calculate optimal number of bits (m) and hash functions (k)
        # m = - (n * ln(p)) / (ln(2)^2)
        n = expected_elements
        p = false_positive_rate
        m = int(math.ceil(-1.0 * (n * math.log(p)) / (math.log(2) ** 2)))
        self.num_bits = max(m, 64)

        # k = (m / n) * ln(2)
        k = int(round((self.num_bits / n) * math.log(2)))
        self.num_hashes = max(1, min(k, 30))

        # Bit array represented as bytearray
        self.byte_count = (self.num_bits + 7) // 8
        self.bit_array = bytearray(self.byte_count)

    def _hashes(self, key: bytes) -> tuple[int, int]:
        """Derive two 64-bit integers from SHA-256 for double hashing."""
        digest = hashlib.sha256(key).digest()
        h1, h2 = struct.unpack(">QQ", digest[:16])
        if h2 == 0:
            h2 = 1
        return h1, h2

    def add(self, key: Union[bytes, str]) -> None:
        """Add a key to the filter."""
        if isinstance(key, str):
            key = key.encode("utf-8")
        h1, h2 = self._hashes(key)
        for i in range(self.num_hashes):
            bit_index = (h1 + i * h2) % self.num_bits
            byte_index = bit_index // 8
            bit_offset = bit_index % 8
            self.bit_array[byte_index] |= (1 << bit_offset)

    def contains(self, key: Union[bytes, str]) -> bool:
        """
        Check if key might be in the set.
        False: 100% guarantee that key is NOT present.
        True: Key is likely present (within false positive probability).
        """
        if isinstance(key, str):
            key = key.encode("utf-8")
        h1, h2 = self._hashes(key)
        for i in range(self.num_hashes):
            bit_index = (h1 + i * h2) % self.num_bits
            byte_index = bit_index // 8
            bit_offset = bit_index % 8
            if not (self.bit_array[byte_index] & (1 << bit_offset)):
                return False
        return True

    def serialize(self) -> bytes:
        """Serialize bloom filter to bytes for writing to SSTable."""
        # Header: num_bits (4B), num_hashes (4B), bit_array_len (4B)
        header = struct.pack(">III", self.num_bits, self.num_hashes, len(self.bit_array))
        return header + bytes(self.bit_array)

    @classmethod
    def deserialize(cls, data: bytes) -> BloomFilter:
        """Deserialize bloom filter from bytes."""
        header_size = struct.calcsize(">III")
        if len(data) < header_size:
            raise ValueError("Corrupted bloom filter header: too short")
        num_bits, num_hashes, array_len = struct.unpack(">III", data[:header_size])
        if len(data) < header_size + array_len:
            raise ValueError("Corrupted bloom filter payload: unexpected EOF")

        bf = cls.__new__(cls)
        bf.num_bits = num_bits
        bf.num_hashes = num_hashes
        bf.byte_count = array_len
        bf.bit_array = bytearray(data[header_size : header_size + array_len])
        bf.expected_elements = 0
        bf.false_positive_rate = 0.0
        return bf

