"""
Probabilistic SkipList implementation for the LSM MemTable.
Provides O(log N) average time complexity for search, insert, and range queries.
"""

from __future__ import annotations
import random
from typing import Generator, List, Optional, Tuple
from lsm.types import Record


class SkipListNode:
    """A node in the SkipList containing a record and forward pointers."""

    def __init__(self, record: Optional[Record], level: int):
        self.record = record
        self.forward: List[Optional[SkipListNode]] = [None] * level


class SkipList:
    """
    SkipList implementation ordered lexicographically by key (bytes).
    Updates to existing keys overwrite with the newer Record.
    """

    MAX_LEVEL = 16
    P_FACTOR = 0.5  # Probability of promoting to the next level

    def __init__(self):
        self.header = SkipListNode(None, self.MAX_LEVEL)
        self.level = 1
        self.count = 0
        self.size_bytes = 0

    def _random_level(self) -> int:
        """Determines the level of a newly inserted node using coin flips."""
        lvl = 1
        while random.random() < self.P_FACTOR and lvl < self.MAX_LEVEL:
            lvl += 1
        return lvl

    def put(self, record: Record) -> int:
        """
        Inserts or updates a record.
        Returns the delta change in byte size.
        """
        update: List[Optional[SkipListNode]] = [None] * self.MAX_LEVEL
        current = self.header

        for i in range(self.level - 1, -1, -1):
            while current.forward[i] and current.forward[i].record.key < record.key:
                current = current.forward[i]
            update[i] = current

        target = current.forward[0]

        # Key already exists: update record
        if target is not None and target.record.key == record.key:
            old_size = len(target.record.key) + (len(target.record.value) if target.record.value else 0)
            new_size = len(record.key) + (len(record.value) if record.value else 0)
            target.record = record
            delta = new_size - old_size
            self.size_bytes += delta
            return delta

        # Key does not exist: insert new node
        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level, new_level):
                update[i] = self.header
            self.level = new_level

        new_node = SkipListNode(record, new_level)
        for i in range(new_level):
            new_node.forward[i] = update[i].forward[i]
            update[i].forward[i] = new_node

        self.count += 1
        record_bytes = len(record.key) + (len(record.value) if record.value else 0) + 32  # 32B node overhead
        self.size_bytes += record_bytes
        return record_bytes

    def get(self, key: bytes) -> Optional[Record]:
        """Looks up a key and returns the associated Record, or None if absent."""
        current = self.header
        for i in range(self.level - 1, -1, -1):
            while current.forward[i] and current.forward[i].record.key < key:
                current = current.forward[i]

        current = current.forward[0]
        if current is not None and current.record.key == key:
            return current.record
        return None

    def __iter__(self) -> Generator[Record, None, None]:
        """Iterates over all records in key order."""
        current = self.header.forward[0]
        while current is not None:
            yield current.record
            current = current.forward[0]

    def range_scan(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
    ) -> Generator[Record, None, None]:
        """
        Yields records in range [start_key, end_key).
        If start_key is None, starts from the beginning.
        If end_key is None, reads to the end.
        """
        current = self.header
        if start_key is not None:
            for i in range(self.level - 1, -1, -1):
                while current.forward[i] and current.forward[i].record.key < start_key:
                    current = current.forward[i]
            current = current.forward[0]
        else:
            current = self.header.forward[0]

        while current is not None:
            if end_key is not None and current.record.key >= end_key:
                break
            yield current.record
            current = current.forward[0]

