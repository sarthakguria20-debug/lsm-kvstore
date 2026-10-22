"""
LSM-Tree Key-Value Engine.
Coordinates Write-Ahead Logging (WAL), MemTable, SSTables, Bloom Filters,
crash recovery, and compaction into a high-performance, crash-resilient store.
"""

from __future__ import annotations
import heapq
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple, Union

from lsm.compaction import Compactor # pyright: ignore[reportMissingImports]
from lsm.memtable import MemTable # pyright: ignore[reportMissingImports]
from lsm.sstable import SSTableReader, SSTableWriter # type: ignore
from lsm.types import Record, RecordType, to_bytes, to_str # type: ignore
from lsm.wal import SyncMode, WALReader, WALWriter # pyright: ignore[reportMissingImports]


class WriteBatch:
    """Groups multiple PUT and DELETE operations for atomic execution."""

    def __init__(self):
        self.operations: List[Tuple[RecordType, bytes, Optional[bytes]]] = []

    def put(self, key: Union[str, bytes], value: Union[str, bytes]) -> WriteBatch:
        self.operations.append((RecordType.PUT, to_bytes(key), to_bytes(value)))
        return self

    def delete(self, key: Union[str, bytes]) -> WriteBatch:
        self.operations.append((RecordType.DELETE, to_bytes(key), None))
        return self

    def clear(self) -> None:
        self.operations.clear()

    def __len__(self) -> int:
        return len(self.operations)


class LSMTreeEngine:
    """
    Production-grade Log-Structured Merge (LSM) Tree Key-Value Engine.
    Features:
    - ACID Durability via Write-Ahead Log (WAL) with CRC32 & fsync
    - O(log N) in-memory writes via SkipList MemTable
    - Immutable SSTables with Bloom Filter and Sparse Index
    - Automatic and manual background compaction
    - Crash consistency: recovers state after sudden power cuts or torn writes
    """

    def __init__(
        self,
        db_dir: Union[str, Path],
        max_memtable_size_bytes: int = 2 * 1024 * 1024,  # 2 MB default flush threshold
        sync_mode: SyncMode = SyncMode.ALWAYS,
        block_size_bytes: int = 4096,
        bloom_fp_rate: float = 0.01,
    ):
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self.max_memtable_size_bytes = max_memtable_size_bytes
        self.sync_mode = sync_mode
        self.block_size_bytes = block_size_bytes
        self.bloom_fp_rate = bloom_fp_rate

        self.lock = threading.RLock()
        self._lock_file_handle = None
        self._acquire_lock()

        # Engine state
        self.next_seq_num = 1
        self.current_lsn = 0
        self.active_memtable: Optional[MemTable] = None
        self.flushing_memtable: Optional[MemTable] = None
        self.active_wal: Optional[WALWriter] = None
        self.sstable_readers: List[SSTableReader] = []  # Index 0 = newest SSTable

        self.compactor = Compactor(self.db_dir)

        # Boot & Recover
        self._recover_and_initialize()

    def _acquire_lock(self) -> None:
        """Acquires directory lock to prevent multi-process database corruption."""
        lock_path = self.db_dir / "LOCK"
        try:
            self._lock_file_handle = open(lock_path, "w")
            # Using fcntl on mac / unix
            import fcntl
            fcntl.flock(self._lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as e:
            raise RuntimeError(f"Database directory {self.db_dir} is locked by another process.") from e

    def _release_lock(self) -> None:
        """Releases the database directory lock."""
        if self._lock_file_handle:
            try:
                import fcntl
                fcntl.flock(self._lock_file_handle, fcntl.LOCK_UN)
                self._lock_file_handle.close()
            except OSError:
                pass
            self._lock_file_handle = None

    def _alloc_seq_num(self) -> int:
        """Atomically allocates a monotonically increasing sequence ID for new files."""
        seq = self.next_seq_num
        self.next_seq_num += 1
        return seq

    def _get_wal_path(self, seq_num: int) -> Path:
        return self.db_dir / f"wal_{seq_num:06d}.log"

    def _get_sstable_path(self, seq_num: int) -> Path:
        return self.db_dir / f"sstable_{seq_num:06d}.sst"

    def _recover_and_initialize(self) -> None:
        """
        Startup sequence:
        1. Clean up stale temporary files (.tmp) from interrupted crashes.
        2. Discover all existing SSTable files and order them newest to oldest.
        3. Discover all existing WAL files and replay them in order.
        4. Validate CRC checksums and truncate torn records at EOF if power failed during write.
        5. Initialize active MemTable and WAL for new incoming writes.
        """
        # Step 1: Clean up interrupted .tmp files
        for tmp_file in self.db_dir.glob("*.tmp"):
            try:
                tmp_file.unlink()
            except OSError:
                pass

        # Step 2: Load SSTables
        sst_pattern = re.compile(r"^sstable_(\d+)\.sst$")
        sstable_files: List[Tuple[int, Path]] = []
        for file in self.db_dir.iterdir():
            match = sst_pattern.match(file.name)
            if match:
                seq = int(match.group(1))
                sstable_files.append((seq, file))
                self.next_seq_num = max(self.next_seq_num, seq + 1)

        # Sort descending by sequence number (newest first)
        sstable_files.sort(key=lambda x: x[0], reverse=True)
        self.sstable_readers = [SSTableReader(path) for _, path in sstable_files]

        # Step 3: Find WAL files
        wal_pattern = re.compile(r"^wal_(\d+)\.log$")
        wal_files: List[Tuple[int, Path]] = []
        for file in self.db_dir.iterdir():
            match = wal_pattern.match(file.name)
            if match:
                seq = int(match.group(1))
                wal_files.append((seq, file))
                self.next_seq_num = max(self.next_seq_num, seq + 1)

        wal_files.sort(key=lambda x: x[0])  # replay oldest WAL to newest WAL

        # Step 4: Replay WAL files into MemTable
        recovered_memtable = MemTable(
            table_id=self.next_seq_num,
            max_size_bytes=self.max_memtable_size_bytes,
        )

        for seq, wal_path in wal_files:
            reader = WALReader(wal_path)
            # Recover records and truncate torn writes at EOF
            records, max_lsn, had_torn_write = reader.recover(truncate_torn_records=True)
            self.current_lsn = max(self.current_lsn, max_lsn)

            for rec in records:
                if rec.record_type == RecordType.PUT:
                    recovered_memtable.put(
                        key=rec.key,
                        value=rec.value,
                        lsn=rec.lsn,
                        timestamp_ns=rec.timestamp_ns,
                    )
                elif rec.record_type == RecordType.DELETE:
                    recovered_memtable.delete(
                        key=rec.key,
                        lsn=rec.lsn,
                        timestamp_ns=rec.timestamp_ns,
                    )

        # Step 5: Flush recovered MemTable if it exceeded capacity, or assign as active
        if recovered_memtable.should_flush() and recovered_memtable.count > 0:
            self._flush_memtable_to_sstable(recovered_memtable)
            # Remove recovered WAL files
            for _, wal_path in wal_files:
                wal_path.unlink(missing_ok=True)
            # Create fresh active memtable
            self._create_new_memtable_and_wal()
        elif wal_files:
            # Continue using the latest WAL file
            latest_seq, latest_wal_path = wal_files[-1]
            # Remove older superseded WAL files if any
            for seq, wal_path in wal_files[:-1]:
                wal_path.unlink(missing_ok=True)
            self.active_memtable = recovered_memtable
            self.active_memtable.table_id = latest_seq # pyright: ignore[reportOptionalMemberAccess]
            self.active_wal = WALWriter(latest_wal_path, sync_mode=self.sync_mode)
        else:
            self._create_new_memtable_and_wal()

    def _create_new_memtable_and_wal(self) -> None:
        """Instantiates a new active MemTable and opens a new WAL file."""
        seq = self._alloc_seq_num()
        wal_path = self._get_wal_path(seq)
        self.active_wal = WALWriter(wal_path, sync_mode=self.sync_mode)
        self.active_memtable = MemTable(
            table_id=seq,
            max_size_bytes=self.max_memtable_size_bytes,
        )

    def _flush_memtable_to_sstable(self, memtable: MemTable) -> SSTableReader:
        """
        Converts an immutable MemTable into an on-disk SSTable.
        Atomic write guaranteed by .tmp file rename and fsync.
        """
        memtable.freeze()
        seq = self._alloc_seq_num()
        sst_path = self._get_sstable_path(seq)

        writer = SSTableWriter(
            filepath=sst_path,
            block_size=self.block_size_bytes,
            expected_keys=max(100, memtable.count),
            fp_rate=self.bloom_fp_rate,
        )
        writer.write_records(iter(memtable))

        new_reader = SSTableReader(sst_path)
        # Prepend to SSTable readers list (newest first)
        self.sstable_readers.insert(0, new_reader)
        return new_reader

    def put(
        self,
        key: Union[str, bytes],
        value: Union[str, bytes],
        sync: Optional[bool] = None,
    ) -> None:
        """
        Writes a key-value pair.
        Durability guarantee:
        1. Writes to WAL on disk with CRC32 checksum.
        2. Forces hardware fsync (if sync mode enabled).
        3. Updates in-memory MemTable.
        4. Triggers flush if MemTable size limit reached.
        """
        k = to_bytes(key)
        v = to_bytes(value)

        with self.lock:
            self.current_lsn += 1
            ts = time.time_ns()

            # 1. Write-Ahead Log append
            self.active_wal.append_record( # pyright: ignore[reportOptionalMemberAccess]
                record_type=RecordType.PUT,
                key=k,
                value=v,
                lsn=self.current_lsn,
                timestamp_ns=ts,
            )

            # Manual override sync if requested
            if sync is True:
                assert self.active_wal is not None
                self.active_wal.sync()

            # 2. In-memory MemTable update
            self.active_memtable.put( # pyright: ignore[reportOptionalMemberAccess]
                key=k,
                value=v,
                lsn=self.current_lsn,
                timestamp_ns=ts,
            )

            # 3. Check flush threshold
            if self.active_memtable.should_flush(): # pyright: ignore[reportOptionalMemberAccess]
                self._trigger_flush()

    def delete(
        self,
        key: Union[str, bytes],
        sync: Optional[bool] = None,
    ) -> None:
        """
        Deletes a key by appending a tombstone record.
        Durability and crash resilience guaranteed via WAL.
        """
        k = to_bytes(key)

        with self.lock:
            self.current_lsn += 1
            ts = time.time_ns()

            # 1. Write-Ahead Log tombstone append
            self.active_wal.append_record( # pyright: ignore[reportOptionalMemberAccess]
                record_type=RecordType.DELETE,
                key=k,
                value=None,
                lsn=self.current_lsn,
                timestamp_ns=ts,
            )

            if sync is True:
                assert self.active_wal is not None
                self.active_wal.sync()

            # 2. In-memory MemTable tombstone insertion
            assert self.active_memtable is not None
            self.active_memtable.delete(
                key=k,
                lsn=self.current_lsn,
                timestamp_ns=ts,
            )

            # 3. Check flush threshold
            assert self.active_memtable is not None
            if self.active_memtable.should_flush():
                self._trigger_flush()

    def write_batch(self, batch: WriteBatch) -> None:
        """
        Atomically executes a batch of operations.
        Wrapped with TXN_BEGIN and TXN_COMMIT in WAL.
        """
        if not batch.operations:
            return

        with self.lock:
            ts = time.time_ns()
            records: List[Record] = []

            for op_type, k, v in batch.operations:
                self.current_lsn += 1
                rec = Record(
                    key=k,
                    value=v,
                    record_type=op_type,
                    timestamp_ns=ts,
                    lsn=self.current_lsn,
                )
                records.append(rec)

            # 1. Atomic WAL batch append
            assert self.active_wal is not None
            self.active_wal.append_batch(records, auto_commit=True)

            # 2. Apply to MemTable
            for rec in records:
                if rec.record_type == RecordType.PUT:
                    assert self.active_memtable is not None
                    self.active_memtable.put(rec.key, rec.value, rec.lsn, rec.timestamp_ns)
                elif rec.record_type == RecordType.DELETE:
                    assert self.active_memtable is not None
                    self.active_memtable.delete(rec.key, rec.lsn, rec.timestamp_ns)

            # 3. Check flush threshold
            assert self.active_memtable is not None
            if self.active_memtable.should_flush():
                self._trigger_flush()

    def _trigger_flush(self) -> None:
        """Freezes active MemTable and WAL, flushes to SSTable, and rotates WAL."""
        old_memtable = self.active_memtable
        old_wal = self.active_wal
        assert old_memtable is not None
        old_wal_path = self._get_wal_path(old_memtable.table_id)

        # 1. Allocate new MemTable and new active WAL
        self._create_new_memtable_and_wal()

        # 2. Persist old MemTable to immutable SSTable
        self._flush_memtable_to_sstable(old_memtable)

        # 3. Close and safely remove old WAL file
        assert old_wal is not None
        old_wal.close()
        old_wal_path.unlink(missing_ok=True)

    def get(self, key: Union[str, bytes], as_str: bool = False) -> Optional[Union[bytes, str]]:
        """
        Retrieves the value for a key.
        Search path:
        1. Active MemTable
        2. Flushing MemTable (if in progress)
        3. SSTables from newest to oldest (using Bloom filter and Sparse index)
        """
        k = to_bytes(key)

        with self.lock:
            # 1. Search Active MemTable
            assert self.active_memtable is not None
            rec = self.active_memtable.get(k)
            if rec is not None:
                if rec.is_tombstone:
                    return None
                return to_str(rec.value) if as_str else rec.value

            # 2. Search Flushing MemTable
            if self.flushing_memtable is not None:
                rec = self.flushing_memtable.get(k)
                if rec is not None:
                    if rec.is_tombstone:
                        return None
                    return to_str(rec.value) if as_str else rec.value

            # 3. Search SSTables from newest to oldest
            for reader in self.sstable_readers:
                rec = reader.get(k)
                if rec is not None:
                    if rec.is_tombstone:
                        return None
                    return to_str(rec.value) if as_str else rec.value

            return None

    def scan(
        self,
        start_key: Optional[Union[str, bytes]] = None,
        end_key: Optional[Union[str, bytes]] = None,
        as_str: bool = False,
    ) -> List[Tuple[Union[bytes, str], Union[bytes, str]]]:
        """
        Range query [start_key, end_key).
        Performs a k-way merge across active MemTable, flushing MemTable, and all SSTables.
        Deduplicates keys, respects tombstones, and outputs sorted key-value pairs.
        """
        sk = to_bytes(start_key) if start_key is not None else None
        ek = to_bytes(end_key) if end_key is not None else None

        with self.lock:
            # Collect streams ordered from newest to oldest
            sources = []
            # Source 0: active MemTable
            assert self.active_memtable is not None
            sources.append(self.active_memtable.range_scan(sk, ek))

            # Source 1: flushing MemTable
            if self.flushing_memtable is not None:
                sources.append(self.flushing_memtable.range_scan(sk, ek))

            # Sources 2..N: SSTables newest to oldest
            for reader in self.sstable_readers:
                sources.append(reader.range_scan(sk, ek))

            # K-way merge using heapq
            # Heap item: (key, priority, record, iterator)
            # Lower priority index = newer source (wins on duplicate key)
            heap: List[Tuple[bytes, int, Record, Generator[Record, None, None]]] = []
            for idx, gen in enumerate(sources):
                try:
                    rec = next(gen)
                    heapq.heappush(heap, (rec.key, idx, rec, gen))
                except StopIteration:
                    pass

            results: List[Tuple[Union[bytes, str], Union[bytes, str]]] = []
            last_emitted_key: Optional[bytes] = None

            while heap:
                key, prio, rec, gen = heapq.heappop(heap)

                try:
                    next_rec = next(gen)
                    heapq.heappush(heap, (next_rec.key, prio, next_rec, gen))
                except StopIteration:
                    pass

                if key == last_emitted_key:
                    # Key already handled by a newer source
                    continue

                last_emitted_key = key

                # Skip if deleted
                if rec.is_tombstone:
                    continue

                k_out = to_str(rec.key) if as_str else rec.key
                v_out = to_str(rec.value) if as_str else rec.value
                results.append((k_out, v_out))

            return results

    def flush(self) -> None:
        """Forces immediate flush of active MemTable to an SSTable."""
        with self.lock:
            if self.active_memtable and self.active_memtable.count > 0:
                self._trigger_flush()

    def compact(self) -> None:
        """
        Executes a major compaction:
        Merges all SSTables into a single optimized SSTable,
        purges obsolete versions, and removes tombstones.
        """
        with self.lock:
            if len(self.sstable_readers) < 2:
                # Nothing or only one SSTable to compact
                return

            seq = self._alloc_seq_num()
            compacted_sst_path = self._get_sstable_path(seq)

            # Compact all existing SSTables
            new_reader = self.compactor.compact(
                readers=self.sstable_readers,
                output_filepath=compacted_sst_path,
                is_bottommost_level=True,
            )

            if new_reader:
                self.sstable_readers = [new_reader]

    def get_memtable_entries(self, limit: int = 100) -> List[Dict[str, Union[str, int, bool]]]:
        """Returns entries currently in the active MemTable for inspection."""
        with self.lock:
            entries = []
            if self.active_memtable:
                for rec in self.active_memtable:
                    entries.append({
                        "key": to_str(rec.key) or "",
                        "value": to_str(rec.value) or "",
                        "is_tombstone": rec.is_tombstone,
                        "lsn": rec.lsn,
                        "timestamp_ns": rec.timestamp_ns,
                    })
                    if len(entries) >= limit:
                        break
            return entries

    def get_sstable_details(self) -> List[Dict[str, Union[str, int, List[str]]]]:
        """Returns structural metadata on all active SSTables."""
        with self.lock:
            details = []
            for r in self.sstable_readers:
                size = r.filepath.stat().st_size if r.filepath.exists() else 0
                sample_keys = [to_str(k) or "" for k in r.index_keys[:5]]
                details.append({
                    "filename": r.filepath.name,
                    "size_bytes": size,
                    "blocks_count": len(r.sparse_index),
                    "sample_first_keys": sample_keys,
                    "bloom_bits": r.bloom.num_bits,
                    "bloom_hashes": r.bloom.num_hashes,
                })
            return details

    def stats(self) -> Dict[str, Union[int, str, List[str]]]:
        """Returns diagnostic statistics of the LSM engine."""
        with self.lock:
            sst_files = [str(r.filepath.name) for r in self.sstable_readers]
            total_sst_bytes = sum(r.filepath.stat().st_size for r in self.sstable_readers if r.filepath.exists())
            wal_bytes = self.active_wal.bytes_written if self.active_wal else 0

            return {
                "active_memtable_entries": self.active_memtable.count if self.active_memtable else 0,
                "active_memtable_bytes": self.active_memtable.size_bytes if self.active_memtable else 0,
                "current_lsn": self.current_lsn,
                "sstable_count": len(self.sstable_readers),
                "sstable_files": sst_files,
                "total_sstable_disk_bytes": total_sst_bytes,
                "active_wal_disk_bytes": wal_bytes,
                "sync_mode": self.sync_mode.value,
            }

    def close(self) -> None:
        """Flushes buffers, closes WAL, releases lock."""
        with self.lock:
            if self.active_wal:
                self.active_wal.close()
                self.active_wal = None
            self._release_lock()

    def __enter__(self) -> LSMTreeEngine:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
