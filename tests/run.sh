#!/bin/bash
# Runs every test against the scripts in this repo. Each test uses a temporary HOME,
# except the check that the installed, running panel passes the peer check.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
for t in test_hook test_agents test_channel test_security test_hash_parity; do
  echo "== $t"
  out=$(python3 "$REPO/tests/$t.py" "$REPO" 2>&1)
  echo "$out" | grep -v "^PASS"
  echo "$out" | grep -q "SOME FAILED\|^FAIL\|Traceback" && fail=1
done
[ $fail = 0 ] && echo "All tests passed." || { echo "Some tests failed."; exit 1; }
