#!/usr/bin/env bash
# Quick launcher for LSM-Tree Key-Value Server & Web Application Dashboard
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PORT=${1:-8000}

echo "Starting LSM-Tree Database Server on port $PORT..."
python3 -m lsm.server --port "$PORT" --dir ./data --open

