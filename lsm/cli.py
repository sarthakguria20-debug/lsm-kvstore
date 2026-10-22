"""
Interactive Command-Line Interface and REPL for the LSM Key-Value Store.
"""

from __future__ import annotations
import argparse
import sys
import json
from pathlib import Path
from lsm.engine import LSMTreeEngine, SyncMode


def run_repl(db: LSMTreeEngine) -> None:
    """Starts an interactive prompt for interacting with the database."""
    print("=" * 60)
    print(" Welcome to LSM-Tree Key-Value Engine REPL")
    print(" Commands:")
    print("   SET <key> <value>   - Store key-value pair")
    print("   GET <key>           - Retrieve value by key")
    print("   DEL <key>           - Delete key (tombstone)")
    print("   SCAN [start] [end]  - Range scan keys")
    print("   FLUSH               - Flush MemTable to SSTable")
    print("   COMPACT             - Run SSTable merge compaction")
    print("   STATS               - Show engine statistics")
    print("   EXIT / QUIT         - Exit REPL")
    print("=" * 60)

    while True:
        try:
            line = input("lsm-kv> ").strip()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].upper()

            if cmd in ("EXIT", "QUIT"):
                print("Exiting...")
                break

            elif cmd == "SET":
                if len(parts) < 3:
                    print("Usage: SET <key> <value>")
                    continue
                key = parts[1]
                value = " ".join(parts[2:])
                db.put(key, value)
                print("OK")

            elif cmd == "GET":
                if len(parts) < 2:
                    print("Usage: GET <key>")
                    continue
                key = parts[1]
                val = db.get(key, as_str=True)
                if val is None:
                    print("(nil)")
                else:
                    print(f'"{val}"')

            elif cmd == "DEL":
                if len(parts) < 2:
                    print("Usage: DEL <key>")
                    continue
                key = parts[1]
                db.delete(key)
                print("OK")

            elif cmd == "SCAN":
                start_key = parts[1] if len(parts) > 1 else None
                end_key = parts[2] if len(parts) > 2 else None
                items = db.scan(start_key, end_key, as_str=True)
                print(f"Total: {len(items)} items")
                for k, v in items:
                    print(f"  {k} => {v}")

            elif cmd == "FLUSH":
                db.flush()
                print("MemTable flushed to new SSTable.")

            elif cmd == "COMPACT":
                db.compact()
                print("SSTables compacted.")

            elif cmd == "STATS":
                stats = db.stats()
                print(json.dumps(stats, indent=2))

            else:
                print(f"Unknown command: '{cmd}'. Type STATS, SET, GET, DEL, SCAN, FLUSH, COMPACT, or EXIT.")

        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="LSM-Tree Key-Value Engine CLI")
    parser.add_argument("--dir", default="./data", help="Directory for database files")
    parser.add_argument("--sync", choices=["always", "none"], default="always", help="WAL sync policy")

    subparsers = parser.add_subparsers(dest="command")

    # repl
    subparsers.add_parser("repl", help="Start interactive REPL")

    # put
    put_parser = subparsers.add_parser("put", help="Put key-value pair")
    put_parser.add_argument("key", help="Key string")
    put_parser.add_argument("value", help="Value string")

    # get
    get_parser = subparsers.add_parser("get", help="Get value by key")
    get_parser.add_argument("key", help="Key string")

    # delete
    del_parser = subparsers.add_parser("delete", help="Delete key")
    del_parser.add_argument("key", help="Key string")

    # scan
    scan_parser = subparsers.add_parser("scan", help="Range scan keys")
    scan_parser.add_argument("start", nargs="?", default=None, help="Start key (inclusive)")
    scan_parser.add_argument("end", nargs="?", default=None, help="End key (exclusive)")

    # flush
    subparsers.add_parser("flush", help="Flush active MemTable to SSTable")

    # compact
    subparsers.add_parser("compact", help="Compact SSTables")

    # stats
    subparsers.add_parser("stats", help="Show database diagnostics")

    args = parser.parse_args()

    db_path = Path(args.dir)
    sync_mode = SyncMode.ALWAYS if args.sync == "always" else SyncMode.NONE

    with LSMTreeEngine(db_path, sync_mode=sync_mode) as db:
        if args.command == "repl" or args.command is None:
            run_repl(db)
        elif args.command == "put":
            db.put(args.key, args.value)
            print("OK")
        elif args.command == "get":
            val = db.get(args.key, as_str=True)
            print("(nil)" if val is None else val)
        elif args.command == "delete":
            db.delete(args.key)
            print("OK")
        elif args.command == "scan":
            results = db.scan(args.start, args.end, as_str=True)
            for k, v in results:
                print(f"{k} => {v}")
        elif args.command == "flush":
            db.flush()
            print("OK")
        elif args.command == "compact":
            db.compact()
            print("OK")
        elif args.command == "stats":
            print(json.dumps(db.stats(), indent=2))


if __name__ == "__main__":
    main()

