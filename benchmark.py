"""
Performance Benchmark and Stress Test for the LSM Key-Value Engine.
Evaluates write throughput, read latency, Bloom filter hit/miss efficiency,
and compaction space reclamation.
"""

from __future__ import annotations
import shutil
import tempfile
import time
from pathlib import Path

from lsm.engine import LSMTreeEngine
from lsm.wal import SyncMode


def run_benchmark():
    temp_dir = tempfile.mkdtemp(prefix="lsm_bench_")
    db_path = Path(temp_dir)
    print("=" * 65)
    print("  LSM-TREE KEY-VALUE ENGINE: PERFORMANCE BENCHMARK")
    print(f"  Directory: {db_path}")
    print("=" * 65)

    try:
        # 1. Benchmark: Writes with SyncMode.NONE (Throughput / MemTable performance)
        num_keys = 10_000
        print(f"\n[1] Sequential Writes ({num_keys:,} entries, SyncMode=NONE):")
        with LSMTreeEngine(db_path, max_memtable_size_bytes=512 * 1024, sync_mode=SyncMode.NONE) as db:
            start_t = time.perf_counter()
            for i in range(num_keys):
                db.put(f"user:{i:06d}", f'{{"id": {i}, "name": "User {i}", "active": true}}')
            duration = time.perf_counter() - start_t
            ops_per_sec = num_keys / duration
            print(f"    Time taken:     {duration:.3f} s")
            print(f"    Throughput:     {ops_per_sec:,.0f} ops/sec")
            print(f"    Avg Latency:    {(duration / num_keys) * 1_000_000:.1f} µs/op")

            # Check stats
            stats = db.stats()
            print(f"    SSTables generated: {stats['sstable_count']}")
            print(f"    Total disk size:    {stats['total_sstable_disk_bytes'] + stats['active_wal_disk_bytes']:,} bytes")

        # 2. Benchmark: Point Read Hits
        print(f"\n[2] Point Read Lookups ({num_keys:,} existing keys):")
        with LSMTreeEngine(db_path) as db:
            start_t = time.perf_counter()
            for i in range(0, num_keys, 2):  # Sample 5,000 keys
                val = db.get(f"user:{i:06d}")
                assert val is not None
            duration = time.perf_counter() - start_t
            sample_count = num_keys // 2
            ops_per_sec = sample_count / duration
            print(f"    Time taken:     {duration:.3f} s")
            print(f"    Read Throughput:{ops_per_sec:,.0f} ops/sec")
            print(f"    Avg Latency:    {(duration / sample_count) * 1_000_000:.1f} µs/op")

        # 3. Benchmark: Negative Lookups (Bloom Filter efficiency)
        print(f"\n[3] Negative Lookups (5,000 non-existent keys, Bloom filter test):")
        with LSMTreeEngine(db_path) as db:
            start_t = time.perf_counter()
            for i in range(sample_count):
                val = db.get(f"non_existent_key_{i:06d}")
                assert val is None
            duration = time.perf_counter() - start_t
            ops_per_sec = sample_count / duration
            print(f"    Time taken:     {duration:.3f} s")
            print(f"    Filter Speed:   {ops_per_sec:,.0f} ops/sec")
            print(f"    Avg Latency:    {(duration / sample_count) * 1_000_000:.1f} µs/op")

        # 4. Benchmark: Range Scan
        print(f"\n[4] Range Scan Queries (100 range scans across MemTable & SSTables):")
        with LSMTreeEngine(db_path) as db:
            start_t = time.perf_counter()
            for i in range(100):
                start_k = f"user:{i * 50:06d}"
                end_k = f"user:{(i + 1) * 50:06d}"
                results = db.scan(start_k, end_k)
            duration = time.perf_counter() - start_t
            print(f"    Time taken:     {duration:.3f} s")
            print(f"    Scan Latency:   {(duration / 100) * 1000:.2f} ms/scan")

        # 5. Benchmark: Overwrites & Compaction Space Reclamation
        print("\n[5] Compaction & Space Reclamation:")
        with LSMTreeEngine(db_path, max_memtable_size_bytes=256 * 1024) as db:
            # Overwrite all keys 3 times to create multiple versions in different SSTables
            for version in range(2, 4):
                for i in range(2000):
                    db.put(f"user:{i:06d}", f'{{"version": {version}, "updated": true}}')
                db.flush()

            pre_stats = db.stats()
            pre_size = pre_stats["total_sstable_disk_bytes"]
            pre_count = pre_stats["sstable_count"]

            print(f"    Pre-compaction SSTable count: {pre_count}")
            print(f"    Pre-compaction Disk size:     {pre_size:,} bytes")

            start_t = time.perf_counter()
            db.compact()
            compaction_time = time.perf_counter() - start_t

            post_stats = db.stats()
            post_size = post_stats["total_sstable_disk_bytes"]
            post_count = post_stats["sstable_count"]
            saved_bytes = pre_size - post_size
            reclamation_pct = (saved_bytes / pre_size) * 100 if pre_size > 0 else 0

            print(f"    Compaction Time:              {compaction_time:.3f} s")
            print(f"    Post-compaction SSTable count:{post_count}")
            print(f"    Post-compaction Disk size:    {post_size:,} bytes")
            print(f"    Reclaimed Space:              {saved_bytes:,} bytes ({reclamation_pct:.1f}%)")

        # 6. Benchmark: Synchronous Durability (fsync on every write)
        fsync_keys = 500
        print(f"\n[6] Strict Hardware Durability ({fsync_keys} writes with os.fsync() on every op):")
        fsync_dir = tempfile.mkdtemp(prefix="lsm_fsync_")
        try:
            with LSMTreeEngine(fsync_dir, sync_mode=SyncMode.ALWAYS) as db:
                start_t = time.perf_counter()
                for i in range(fsync_keys):
                    db.put(f"durable:{i:04d}", f"val_{i}")
                duration = time.perf_counter() - start_t
                ops_per_sec = fsync_keys / duration
                print(f"    Time taken:     {duration:.3f} s")
                print(f"    Durability IOPS:{ops_per_sec:,.0f} physical syncs/sec")
                print(f"    Disk Sync Cost: {(duration / fsync_keys) * 1000:.2f} ms/fsync")
        finally:
            shutil.rmtree(fsync_dir, ignore_errors=True)

        print("\n" + "=" * 65)
        print("  BENCHMARK COMPLETED SUCCESSFULLY")
        print("=" * 65)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_benchmark()

