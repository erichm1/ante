#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Hard load test runner
#
# Usage:
#   ./load_test/run.sh                 # default: 100 users, 2 min
#   ./load_test/run.sh 50 5 90s        # 50 users, spawn 5/s, run 90s
#   HOST=http://staging:8000 ./load_test/run.sh
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# -- activate venv if not already in one
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  source "$PROJECT_DIR/../env/bin/activate"
fi

HOST="${HOST:-http://127.0.0.1:8000}"

# create/reset the dedicated load-test user and fixtures before starting
python load_test/setup_fixtures.py
USERS="${1:-100}"
SPAWN_RATE="${2:-10}"
RUN_TIME="${3:-2m}"
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
CSV_PREFIX="$RESULTS_DIR/$TIMESTAMP"

echo "======================================================"
echo "  datamigrator — hard load test"
echo "  host       : $HOST"
echo "  users      : $USERS   spawn-rate: $SPAWN_RATE/s"
echo "  duration   : $RUN_TIME"
echo "  results in : $RESULTS_DIR/"
echo "======================================================"
echo ""

cd "$PROJECT_DIR"

locust \
  -f load_test/locustfile.py \
  --headless \
  --host "$HOST" \
  -u "$USERS" \
  -r "$SPAWN_RATE" \
  -t "$RUN_TIME" \
  --csv "$CSV_PREFIX" \
  --html "$RESULTS_DIR/${TIMESTAMP}_report.html" \
  --exit-code-on-error 1

echo ""
echo "======================================================"
echo "  Done. HTML report: $RESULTS_DIR/${TIMESTAMP}_report.html"
echo "======================================================"
