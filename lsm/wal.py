"""
Write-Ahead Logging (WAL) engine.
Guarantees durability (ACID D) and crash consistency using CRC32 checksums,
atomic framing, and explicit OS fsync calls.
"""

from __future__ import annotations
import os
import struct
import time
import zlib
from enum import Enum
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from lsm.types import Record, RecordType


class SyncMode(str, Enum):
    """fsync policy for the WAL."""
    ALWAYS = "always"      # fsync on every write (guarantees zero data loss on power failure)
    NONE = "none"          # rely on OS page cache (fastest, durability depends on OS flush)


# Binary Frame format:
# [CRC32: 4B][PAYLOAD_LEN: 4B]
# Payload:
# [TYPE: 1B][TIMESTAMP_NS: 8B][LSN: 8B][KEY_LEN: 4B][VAL_LEN: 4B][KEY][VAL]
HEADER_FORMAT = ">II"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
PAYLOAD_META_FORMAT = ">BQQII"
PAYLOAD_META_SIZE = struct.calcsize(PAYLOAD_META_FORMAT)


class WALRecordCorruptedError(Exception):
    """Raised when a WAL record fails checksum or format verification."""
    pass


class WALWriter:
    """
    Appends structured records to a Write-Ahead Log on disk.
    Each record is guarded with a CRC32 checksum and length framing.
    """

    def __init__(self, filepath: Path, sync_mode: SyncMode = SyncMode.ALWAYS):
        self.filepath = Path(filepath)
        self.sync_mode = sync_mode
        self.file = open(self.filepath, "a+b")
        self.fd = self.file.fileno()
        self.bytes_written = self.file.tell()

    def append_record(
        self,
        record_type: RecordType,
        key: bytes,
        value: Optional[bytes] = None,
        lsn: int = 0,
        timestamp_ns: Optional[int] = None,
    ) -> int:
        """Appends a single record with CRC32 checksum and optional fsync."""
        if timestamp_ns is None:
            timestamp_ns = time.time_ns()

        val_bytes = value if value is not None else b""
        payload_meta = struct.pack(
            PAYLOAD_META_FORMAT,
            record_type.value,
            timestamp_ns,
            lsn,
            len(key),
            len(val_bytes),
        )
        payload = payload_meta + key + val_bytes
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = struct.pack(HEADER_FORMAT, crc, len(payload))

        frame = header + payload
        self.file.write(frame)
        self.bytes_written += len(frame)

        if self.sync_mode == SyncMode.ALWAYS:
            self.sync()

        return self.bytes_written

    def append_batch(
        self,
        records: List[Record],
        auto_commit: bool = True,
    ) -> int:
        """
        Atomically appends a batch of records.
        Surrounds the batch with TXN_BEGIN and TXN_COMMIT framing.
        """
        if not records:
            return self.bytes_written

        # TXN_BEGIN
        self.append_record(
            record_type=RecordType.TXN_BEGIN,
            key=b"",
            value=None,
            lsn=records[0].lsn,
            timestamp_ns=records[0].timestamp_ns,
        )

        # Batch contents
        for rec in records:
            self.append_record(
                record_type=rec.record_type,
                key=rec.key,
                value=rec.value,
                lsn=rec.lsn,
                timestamp_ns=rec.timestamp_ns,
            )

        # TXN_COMMIT
        if auto_commit:
            self.append_record(
                record_type=RecordType.TXN_COMMIT,
                key=b"",
                value=None,
                lsn=records[-1].lsn,
                timestamp_ns=records[-1].timestamp_ns,
            )

        # Ensure all records in the batch are physically on disk
        if self.sync_mode == SyncMode.ALWAYS:
            self.sync()

        return self.bytes_written

    def sync(self) -> None:
        """Flush user-space buffers and trigger OS fsync to persist to disk hardware."""
        self.file.flush()
        os.fsync(self.fd)

    def close(self) -> None:
        """Safely sync and close the log file."""
        if not self.file.closed:
            try:
                self.sync()
            except OSError:
                pass
            self.file.close()


class WALReader:
    """
    Reads and validates a Write-Ahead Log.
    Detects torn writes, bit flips, and truncated records from sudden power cuts,
    and enables clean recovery up to the last known durable record.
    """

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)

    def recover(
        self,
        truncate_torn_records: bool = True,
    ) -> Tuple[List[Record], int, bool]:
        """
        Replays the log, validating CRC checksums.
        If a torn record is detected at EOF (e.g. power lost during write),
        it optionally truncates the log file to discard the partial write and restore consistency.

        Returns:
            (valid_records, max_lsn, had_corruption_or_torn_write)
        """
        if not self.filepath.exists():
            return [], 0, False

        valid_records: List[Record] = []
        in_txn = False
        txn_buffer: List[Record] = []
        max_lsn = 0
        last_valid_pos = 0
        torn_write_detected = False

        file_size = self.filepath.stat().st_size
        if file_size == 0:
            return [], 0, False

        with open(self.filepath, "rb") as f:
            while True:
                pos_before_record = f.tell()
                header_bytes = f.read(HEADER_SIZE)
                if not header_bytes:
                    # Clean EOF
                    break

                if len(header_bytes) < HEADER_SIZE:
                    # Torn header at EOF
                    torn_write_detected = True
                    break

                expected_crc, payload_len = struct.unpack(HEADER_FORMAT, header_bytes)

                # Check if payload is complete
                payload_bytes = f.read(payload_len)
                if len(payload_bytes) < payload_len:
                    # Torn payload at EOF
                    torn_write_detected = True
                    break

                # Validate CRC32 checksum
                actual_crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    # Corrupted record (bit rot or partial write)
                    torn_write_detected = True
                    break

                # Parse payload
                if len(payload_bytes) < PAYLOAD_META_SIZE:
                    torn_write_detected = True
                    break

                type_val, ts, lsn, klen, vlen = struct.unpack(
                    PAYLOAD_META_FORMAT, payload_bytes[:PAYLOAD_META_SIZE]
                )

                if len(payload_bytes) != PAYLOAD_META_SIZE + klen + vlen:
                    torn_write_detected = True
                    break

                key_bytes = payload_bytes[PAYLOAD_META_SIZE : PAYLOAD_META_SIZE + klen]
                val_bytes = payload_bytes[PAYLOAD_META_SIZE + klen :]

                rec_type = RecordType(type_val)
                rec = Record(
                    key=key_bytes,
                    value=val_bytes if rec_type == RecordType.PUT else None,
                    record_type=rec_type,
                    timestamp_ns=ts,
                    lsn=lsn,
                )

                max_lsn = max(max_lsn, lsn)

                # Handle Transactions / Batches
                if rec_type == RecordType.TXN_BEGIN:
                    in_txn = True
                    txn_buffer = []
                elif rec_type == RecordType.TXN_COMMIT:
                    if in_txn:
                        valid_records.extend(txn_buffer)
                        in_txn = False
                        txn_buffer = []
                else:
                    if in_txn:
                        txn_buffer.append(rec)
                    else:
                        valid_records.append(rec)

                # Record processed successfully
                last_valid_pos = f.tell()

        # If a transaction was open and never received TXN_COMMIT before EOF/crash,
        # it is discarded (atomicity guarantee)
        if in_txn:
            torn_write_detected = True

        # Truncate torn write if requested to bring the file into a pristine state
        if torn_write_detected and truncate_torn_records:
            with open(self.filepath, "r+b") as f:
                f.truncate(last_valid_pos)

        return valid_records, max_lsn, torn_write_detected

