"""
Compaction engine for the LSM-Tree.
Merges multiple SSTables into a single consolidated SSTable,
deduplicating overwritten keys and reclaiming disk space from tombstones.
"""

from __future__ import annotations
import heapq
from pathlib import Path
from typing import Generator, List, Optional, Tuple

from lsm.sstable import SSTableReader, SSTableWriter
from lsm.types import Record, RecordType


class Compactor:
    """
    Executes k-way merge compaction across multiple SSTables.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    def compact(
        self,
        readers: List[SSTableReader],
        output_filepath: Path,
        is_bottommost_level: bool = True,
    ) -> Optional[SSTableReader]:
        """
        Merges a list of SSTables (ordered from newest to oldest) into a single SSTable.
        Discards superseded versions and purges tombstones (if bottommost level).
        """
        if not readers:
            return None

        # Build k-way merge iterator.
        # We need to process keys in ascending order.
        # If the same key exists in multiple SSTables, the newest one must win.
        # Reader index 0 = newest, index len-1 = oldest.
        # Heap elements: (record.key, reader_index, record)
        heap: List[Tuple[bytes, int, Record, Generator[Record, None, None]]] = []

        generators = [reader.iter_all() for reader in readers]
        for idx, gen in enumerate(generators):
            try:
                rec = next(gen)
                # Note: Python heapq sorts tuples element-wise.
                # Sort by: key ASC, reader_index ASC (smaller index = newer sstable)
                heapq.heappush(heap, (rec.key, idx, rec, gen))
            except StopIteration:
                pass

        def merged_records_generator() -> Generator[Record, None, None]:
            last_emitted_key: Optional[bytes] = None

            while heap:
                key, reader_idx, rec, gen = heapq.heappop(heap)

                # Fetch next record from the generator that produced this record
                try:
                    next_rec = next(gen)
                    heapq.heappush(heap, (next_rec.key, reader_idx, next_rec, gen))
                except StopIteration:
                    pass

                # If this key was already emitted in this compaction pass,
                # skip it because the earlier one came from a newer SSTable.
                if key == last_emitted_key:
                    continue

                last_emitted_key = key

                # If tombstone and bottommost level, purge it completely
                if rec.is_tombstone and is_bottommost_level:
                    continue

                yield rec

        # Write merged records to new SSTable
        writer = SSTableWriter(output_filepath, expected_keys=max(1000, len(readers) * 1000))
        written_count = writer.write_records(merged_records_generator())

        # Safely remove old SSTables now that the compacted file is durable on disk
        for r in readers:
            try:
                r.filepath.unlink(missing_ok=True)
            except OSError:
                pass

        return SSTableReader(output_filepath)

