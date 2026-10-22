"""
In-memory MemTable coordinating with SkipList and tracking memory size.
"""

from __future__ import annotations
import time
from typing import Generator, Optional
from lsm.types import Record, RecordType
from lsm.skiplist import SkipList


class MemTable:
    """
    In-memory mutable buffer for writes.
    Once it exceeds max_size_bytes, it is frozen and flushed to an SSTable.
    """

    def __init__(self, table_id: int, max_size_bytes: int = 4 * 1024 * 1024):
        self.table_id = table_id
        self.max_size_bytes = max_size_bytes
        self.skiplist = SkipList()
        self.is_frozen = False
        self.created_at_ns = time.time_ns()

    @property
    def size_bytes(self) -> int:
        return self.skiplist.size_bytes

    @property
    def count(self) -> int:
        return self.skiplist.count

    def should_flush(self) -> bool:
        """Returns True if the MemTable has reached its capacity threshold."""
        return self.skiplist.size_bytes >= self.max_size_bytes

    def put(
        self,
        key: bytes,
        value: bytes,
        lsn: int = 0,
        timestamp_ns: Optional[int] = None,
    ) -> None:
        """Inserts or updates a key-value record."""
        if self.is_frozen:
            raise RuntimeError("Cannot write to a frozen MemTable")
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()

        rec = Record(
            key=key,
            value=value,
            record_type=RecordType.PUT,
            timestamp_ns=timestamp_ns,
            lsn=lsn,
        )
        self.skiplist.put(rec)

    def delete(
        self,
        key: bytes,
        lsn: int = 0,
        timestamp_ns: Optional[int] = None,
    ) -> None:
        """Inserts a tombstone record for key deletion."""
        if self.is_frozen:
            raise RuntimeError("Cannot write to a frozen MemTable")
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()

        tombstone = Record(
            key=key,
            value=None,
            record_type=RecordType.DELETE,
            timestamp_ns=timestamp_ns,
            lsn=lsn,
        )
        self.skiplist.put(tombstone)

    def get(self, key: bytes) -> Optional[Record]:
        """Looks up a key in the MemTable."""
        return self.skiplist.get(key)

    def freeze(self) -> None:
        """Marks the MemTable as immutable, preparing it for disk flush."""
        self.is_frozen = True

    def __iter__(self) -> Generator[Record, None, None]:
        """Iterates over all records in sorted key order."""
        return iter(self.skiplist)

    def range_scan(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
    ) -> Generator[Record, None, None]:
        """Scans records in range [start_key, end_key)."""
        return self.skiplist.range_scan(start_key, end_key)

