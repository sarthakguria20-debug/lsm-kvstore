"""
SSTable (Sorted String Table) implementation with Data Blocks, Sparse Index,
Bloom Filter, and fixed-size Footer for high-performance point & range queries.
"""

from __future__ import annotations
import bisect
import os
import struct
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from lsm.bloom import BloomFilter # pyright: ignore[reportMissingImports]
from lsm.types import Record, RecordType # type: ignore

MAGIC_HEADER = b"SST01\x00\x00\x00"
MAGIC_FOOTER = b"SST01END"
FOOTER_FORMAT = ">QQQQ8s"
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)

RECORD_HEADER_FORMAT = ">BQQII"
RECORD_HEADER_SIZE = struct.calcsize(RECORD_HEADER_FORMAT)


class SSTableWriter:
    """
    Writes a sorted sequence of Records to an immutable SSTable file.
    Creates data blocks, sparse index entries, and a Bloom filter.
    Guarantees atomic file creation via .tmp file and os.replace().
    """

    def __init__(
        self,
        filepath: Path,
        block_size: int = 4096,
        expected_keys: int = 1000,
        fp_rate: float = 0.01,
    ):
        self.final_filepath = Path(filepath)
        self.tmp_filepath = Path(str(filepath) + ".tmp")
        self.block_size = block_size
        self.bloom = BloomFilter(expected_keys, fp_rate)
        self.sparse_index: List[Tuple[bytes, int, int]] = []  # (first_key, offset, size)

    def write_records(self, records: Generator[Record, None, None] | List[Record]) -> int:
        """
        Writes sorted records to the SSTable and atomically commits the file.
        Returns the total number of records written.
        """
        self.tmp_filepath.parent.mkdir(parents=True, exist_ok=True)
        count = 0

        with open(self.tmp_filepath, "wb") as f:
            # 1. Write Header
            f.write(MAGIC_HEADER)

            # 2. Write Data Blocks
            current_block_records: List[bytes] = []
            current_block_bytes = 0
            current_block_first_key: Optional[bytes] = None
            block_start_offset = f.tell()

            for rec in records:
                count += 1
                self.bloom.add(rec.key)

                val_bytes = rec.value if rec.value is not None else b""
                rec_meta = struct.pack(
                    RECORD_HEADER_FORMAT,
                    rec.record_type.value,
                    rec.timestamp_ns,
                    rec.lsn,
                    len(rec.key),
                    len(val_bytes),
                )
                rec_bytes = rec_meta + rec.key + val_bytes

                if current_block_first_key is None:
                    current_block_first_key = rec.key

                current_block_records.append(rec_bytes)
                current_block_bytes += len(rec_bytes)

                # Flush block if size threshold reached
                if current_block_bytes >= self.block_size:
                    block_data = b"".join(current_block_records)
                    f.write(block_data)
                    self.sparse_index.append(
                        (current_block_first_key, block_start_offset, len(block_data)) # pyright: ignore[reportArgumentType]
                    )
                    block_start_offset = f.tell()
                    current_block_records = []
                    current_block_bytes = 0
                    current_block_first_key = None

            # Flush any remaining records in final block
            if current_block_records and current_block_first_key is not None:
                block_data = b"".join(current_block_records)
                f.write(block_data)
                self.sparse_index.append(
                    (current_block_first_key, block_start_offset, len(block_data))
                )

            # 3. Write Sparse Index Section
            index_offset = f.tell()
            for key, offset, size in self.sparse_index:
                # Format: [KEY_LEN: 4B][KEY][OFFSET: 8B][SIZE: 8B]
                entry_header = struct.pack(">I", len(key))
                entry_trailer = struct.pack(">QQ", offset, size)
                f.write(entry_header + key + entry_trailer)
            index_size = f.tell() - index_offset

            # 4. Write Bloom Filter Section
            bloom_offset = f.tell()
            bloom_bytes = self.bloom.serialize()
            f.write(bloom_bytes)
            bloom_size = len(bloom_bytes)

            # 5. Write Footer
            footer_bytes = struct.pack(
                FOOTER_FORMAT,
                index_offset,
                index_size,
                bloom_offset,
                bloom_size,
                MAGIC_FOOTER,
            )
            f.write(footer_bytes)

            # 6. Flush and fsync temporary file to disk
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename ensures readers never see a partially written SSTable
        os.replace(self.tmp_filepath, self.final_filepath)
        return count


class SSTableReader:
    """
    Reads an SSTable file.
    Caches the Sparse Index and Bloom Filter in memory for instant lookups.
    """

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)
        self.bloom: BloomFilter
        # Sparse index: list of (first_key, offset, size)
        self.sparse_index: List[Tuple[bytes, int, int]] = []
        self.index_keys: List[bytes] = []
        self._load_metadata()

    def _load_metadata(self) -> None:
        """Reads Header, Footer, Sparse Index, and Bloom Filter into memory."""
        file_size = self.filepath.stat().st_size
        if file_size < len(MAGIC_HEADER) + FOOTER_SIZE:
            raise ValueError(f"SSTable file too small: {self.filepath}")

        with open(self.filepath, "rb") as f:
            # Check Header
            header = f.read(len(MAGIC_HEADER))
            if header != MAGIC_HEADER:
                raise ValueError(f"Invalid SSTable header magic in {self.filepath}")

            # Read Footer
            f.seek(file_size - FOOTER_SIZE)
            footer_bytes = f.read(FOOTER_SIZE)
            idx_offset, idx_size, bf_offset, bf_size, footer_magic = struct.unpack(
                FOOTER_FORMAT, footer_bytes
            )

            if footer_magic != MAGIC_FOOTER:
                raise ValueError(f"Invalid SSTable footer magic in {self.filepath}")

            # Load Bloom Filter
            f.seek(bf_offset)
            bf_data = f.read(bf_size)
            self.bloom = BloomFilter.deserialize(bf_data)

            # Load Sparse Index
            f.seek(idx_offset)
            idx_data = f.read(idx_size)
            pos = 0
            while pos < idx_size:
                klen = struct.unpack(">I", idx_data[pos : pos + 4])[0]
                pos += 4
                key = idx_data[pos : pos + klen]
                pos += klen
                offset, size = struct.unpack(">QQ", idx_data[pos : pos + 16])
                pos += 16
                self.sparse_index.append((key, offset, size))
                self.index_keys.append(key)

    def _parse_block(self, block_data: bytes) -> List[Record]:
        """Parses raw block bytes into a list of Record objects."""
        records: List[Record] = []
        pos = 0
        total_len = len(block_data)

        while pos + RECORD_HEADER_SIZE <= total_len:
            type_val, ts, lsn, klen, vlen = struct.unpack(
                RECORD_HEADER_FORMAT, block_data[pos : pos + RECORD_HEADER_SIZE]
            )
            pos += RECORD_HEADER_SIZE

            if pos + klen + vlen > total_len:
                break

            key = block_data[pos : pos + klen]
            pos += klen
            val = block_data[pos : pos + vlen]
            pos += vlen

            if type_val not in (RecordType.PUT.value, RecordType.DELETE.value):
                continue

            rec_type = RecordType(type_val)
            records.append(
                Record(
                    key=key,
                    value=val if rec_type == RecordType.PUT else None,
                    record_type=rec_type,
                    timestamp_ns=ts,
                    lsn=lsn,
                )
            )

        return records

    def get(self, key: bytes) -> Optional[Record]:
        """
        Point lookup:
        1. Query Bloom filter (avoid disk I/O if absent).
        2. Binary search sparse index.
        3. Read candidate block and scan for key.
        """
        # Step 1: Bloom filter check
        if not self.bloom.contains(key):
            return None

        if not self.sparse_index:
            return None

        # Step 2: Binary search sparse index to locate candidate block
        # index_keys contains the first key of each block
        idx = bisect.bisect_right(self.index_keys, key) - 1
        if idx < 0:
            # Key is smaller than the very first key in the SSTable
            return None

        _, offset, size = self.sparse_index[idx]

        # Step 3: Read single data block from disk
        with open(self.filepath, "rb") as f:
            f.seek(offset)
            block_data = f.read(size)

        records = self._parse_block(block_data)
        for rec in records:
            if rec.key == key:
                return rec
            if rec.key > key:
                # Keys are strictly sorted within the block; can stop early
                break

        return None

    def iter_all(self) -> Generator[Record, None, None]:
        """Sequentially streams all records in the SSTable."""
        with open(self.filepath, "rb") as f:
            for _, offset, size in self.sparse_index:
                f.seek(offset)
                block_data = f.read(size)
                for rec in self._parse_block(block_data):
                    yield rec

    def range_scan(
        self,
        start_key: Optional[bytes] = None,
        end_key: Optional[bytes] = None,
    ) -> Generator[Record, None, None]:
        """Scans records in range [start_key, end_key)."""
        if not self.sparse_index:
            return

        start_block_idx = 0
        if start_key is not None:
            idx = bisect.bisect_right(self.index_keys, start_key) - 1
            start_block_idx = max(0, idx)

        with open(self.filepath, "rb") as f:
            for i in range(start_block_idx, len(self.sparse_index)):
                _, offset, size = self.sparse_index[i]
                f.seek(offset)
                block_data = f.read(size)
                records = self._parse_block(block_data)

                for rec in records:
                    if start_key is not None and rec.key < start_key:
                        continue
                    if end_key is not None and rec.key >= end_key:
                        return
                    yield rec
