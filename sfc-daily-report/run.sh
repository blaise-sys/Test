#!/usr/bin/env bash
# Build the daily report from payload.json. Non-zero exit means DO NOT EMAIL.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f payload.json ]; then
  echo "FAIL: payload.json missing" >&2
  exit 1
fi
python3 build.py
