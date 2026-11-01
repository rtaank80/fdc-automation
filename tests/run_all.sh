#!/usr/bin/env bash
# Run every offline test. Needs Python 3.10+ with `pip install -r requirements.txt`, and Node 18+.
set -e
cd "$(dirname "$0")/.."
python3 tests/test_content.py
python3 tests/test_flow.py
node tests/worker.test.mjs
echo "ALL TESTS PASSED"
