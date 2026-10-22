"""
Core data types, enums, and structures for the LSM-Tree Key-Value Engine.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Union


class RecordType(IntEnum):
    """Type tag for records written to WAL and SSTables."""
    PUT = 1
    DELETE = 2        # Tombstone indicating key deletion
    TXN_BEGIN = 3     # Marker for multi-operation batch start
    TXN_COMMIT = 4    # Marker for multi-operation batch commit


@dataclass(frozen=True)
class Record:
    """Represents a single entry in the engine (MemTable, WAL, or SSTable)."""
    key: bytes
    value: Optional[bytes]
    record_type: RecordType
    timestamp_ns: int
    lsn: int = 0  # Log sequence number (monotonically increasing)

    @property
    def is_tombstone(self) -> bool:
        return self.record_type == RecordType.DELETE

    def __lt__(self, other: Record) -> bool:
        """Compare records primarily by key ascending, then by timestamp descending."""
        if self.key != other.key:
            return self.key < other.key
        return self.timestamp_ns > other.timestamp_ns


def to_bytes(data: Union[str, bytes]) -> bytes:
    """Helper to convert string or bytes to bytes."""
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise TypeError(f"Expected str or bytes, got {type(data).__name__}")


def to_str(data: Optional[bytes]) -> Optional[str]:
    """Helper to convert bytes to string if possible, otherwise string repr."""
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return repr(data)

