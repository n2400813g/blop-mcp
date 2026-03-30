#!/usr/bin/env bash
# smoke_local.sh — local end-to-end smoke test for the blop-mcp tool chain.
#
# Usage:
#   GOOGLE_API_KEY=<key> bash scripts/smoke_local.sh https://your-app.example.com
#
# Optional env vars:
#   BLOP_LLM_PROVIDER  — defaults to google (set to anthropic/openai with matching key)
#   SMOKE_POLL_TIMEOUT — seconds to wait for run completion (default: 300)
#   SMOKE_PROFILE      — auth profile name to pass to tools (optional)
#
# Exit codes:
#   0  — all assertions passed
#   1  — one or more assertions failed
#   2  — bad invocation / missing required env

set -uo pipefail

# ---------------------------------------------------------------------------
# Args / env
# ---------------------------------------------------------------------------
TARGET_URL="${1:-${BLOP_APP_URL:-${APP_BASE_URL:-}}}"
TARGET_URL="${TARGET_URL%%/}"   # strip trailing slash

if [[ -z "$TARGET_URL" ]]; then
  echo "ERROR: TARGET_URL required. Pass as first arg or set BLOP_APP_URL." >&2
  echo "Usage: GOOGLE_API_KEY=... bash scripts/smoke_local.sh https://your-app.example.com" >&2
  exit 2
fi

LLM_PROVIDER="${BLOP_LLM_PROVIDER:-google}"
POLL_TIMEOUT="${SMOKE_POLL_TIMEOUT:-300}"
PROFILE_NAME="${SMOKE_PROFILE:-}"

PASS=0
FAIL=0
STEP_ERRORS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
step_pass() {
  local name="$1"
  PASS=$(( PASS + 1 ))
  echo "  PASS  [$name]"
}

step_fail() {
  local name="$1"
  local msg="$2"
  FAIL=$(( FAIL + 1 ))
  STEP_ERRORS+=("[$name] $msg")
  echo "  FAIL  [$name]: $msg"
}

separator() {
  echo "------------------------------------------------------------"
}

# ---------------------------------------------------------------------------
# Step 0 — API key check
# ---------------------------------------------------------------------------
separator
echo "STEP 0: Check LLM API key"

case "$LLM_PROVIDER" in
  google)
    if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
      step_fail "api_key_check" "GOOGLE_API_KEY is not set (LLM_PROVIDER=google)"
    else
      step_pass "api_key_check"
    fi
    ;;
  anthropic)
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      step_fail "api_key_check" "ANTHROPIC_API_KEY is not set (LLM_PROVIDER=anthropic)"
    else
      step_pass "api_key_check"
    fi
    ;;
  openai)
    if [[ -z "${OPENAI_API_KEY:-}" ]]; then
      step_fail "api_key_check" "OPENAI_API_KEY is not set (LLM_PROVIDER=openai)"
    else
      step_pass "api_key_check"
    fi
    ;;
  *)
    step_fail "api_key_check" "Unknown BLOP_LLM_PROVIDER='$LLM_PROVIDER'; expected google/anthropic/openai"
    ;;
esac

# ---------------------------------------------------------------------------
# Step 1 — validate_release_setup
# ---------------------------------------------------------------------------
separator
echo "STEP 1: validate_release_setup (TARGET_URL=$TARGET_URL)"

VALIDATE_OUT=$(uv run python -c "
import asyncio, json, sys
sys.path.insert(0, 'src')
import blop.config  # loads .env

from blop.tools.validate import validate_release_setup

profile_name = '$PROFILE_NAME' or None

result = asyncio.run(
    validate_release_setup(
        app_url='$TARGET_URL',
        profile_name=profile_name if profile_name else None,
    )
)

if hasattr(result, 'model_dump'):
    print(json.dumps(result.model_dump()))
elif isinstance(result, (dict, list)):
    print(json.dumps(result))
else:
    print(str(result))
" 2>&1) || true

if echo "$VALIDATE_OUT" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if 'status' in d else 1)" 2>/dev/null; then
  VALIDATE_STATUS=$(echo "$VALIDATE_OUT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))")
  if [[ "$VALIDATE_STATUS" == "blocked" ]]; then
    step_fail "validate_release_setup" "status=blocked — fix blockers before running smoke test. Output: ${VALIDATE_OUT:0:400}"
  else
    step_pass "validate_release_setup"
    echo "    status=$VALIDATE_STATUS"
  fi
else
  step_fail "validate_release_setup" "unexpected output (no 'status' key): ${VALIDATE_OUT:0:400}"
fi

# ---------------------------------------------------------------------------
# Step 2 — discover_critical_journeys
# ---------------------------------------------------------------------------
separator
echo "STEP 2: discover_critical_journeys (max_depth=1, max_pages=3)"

JOURNEYS_OUT=$(uv run python -c "
import asyncio, json, sys
sys.path.insert(0, 'src')
import blop.config

from blop.tools.journeys import discover_critical_journeys

profile_name = '$PROFILE_NAME' or None

result = asyncio.run(
    discover_critical_journeys(
        app_url='$TARGET_URL',
        profile_name=profile_name if profile_name else None,
        max_depth=1,
        max_pages=3,
    )
)

if hasattr(result, 'model_dump'):
    print(json.dumps(result.model_dump()))
elif isinstance(result, (dict, list)):
    print(json.dumps(result))
else:
    print(str(result))
" 2>&1) || true

if echo "$JOURNEYS_OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  JOURNEY_COUNT=$(echo "$JOURNEYS_OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'error' in d:
    print(-1)
else:
    journeys = d.get('journeys') or d.get('data', {}).get('journeys') or []
    print(len(journeys))
" 2>/dev/null || echo "-1")

  if [[ "$JOURNEY_COUNT" -ge 1 ]]; then
    step_pass "discover_critical_journeys"
    echo "    journeys_found=$JOURNEY_COUNT"
  elif [[ "$JOURNEY_COUNT" -eq 0 ]]; then
    step_fail "discover_critical_journeys" "0 journeys returned (expected >= 1)"
  else
    step_fail "discover_critical_journeys" "error in response: ${JOURNEYS_OUT:0:400}"
  fi
else
  step_fail "discover_critical_journeys" "non-JSON output: ${JOURNEYS_OUT:0:400}"
fi

# ---------------------------------------------------------------------------
# Step 3 — run_release_check  (returns run_id immediately; actual work is async)
# ---------------------------------------------------------------------------
separator
echo "STEP 3: run_release_check (headless=True)"

RUN_CHECK_OUT=$(uv run python -c "
import asyncio, json, sys
sys.path.insert(0, 'src')
import blop.config

from blop.tools.release_check import run_release_check

profile_name = '$PROFILE_NAME' or None

result = asyncio.run(
    run_release_check(
        app_url='$TARGET_URL',
        profile_name=profile_name if profile_name else None,
        headless=True,
        mode='replay',
    )
)

if hasattr(result, 'model_dump'):
    print(json.dumps(result.model_dump()))
elif isinstance(result, (dict, list)):
    print(json.dumps(result))
else:
    print(str(result))
" 2>&1) || true

RUN_ID=""
if echo "$RUN_CHECK_OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
  RUN_ID=$(echo "$RUN_CHECK_OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
if 'error' in d:
    sys.exit(1)
run_id = d.get('run_id') or (d.get('data') or {}).get('run_id') or ''
print(run_id)
" 2>/dev/null || echo "")

  if [[ -n "$RUN_ID" ]]; then
    step_pass "run_release_check"
    echo "    run_id=$RUN_ID"
  else
    step_fail "run_release_check" "missing run_id in response: ${RUN_CHECK_OUT:0:400}"
  fi
else
  step_fail "run_release_check" "non-JSON output: ${RUN_CHECK_OUT:0:400}"
fi

# ---------------------------------------------------------------------------
# Step 4 — poll get_test_results until completed (max POLL_TIMEOUT seconds)
# ---------------------------------------------------------------------------
separator
echo "STEP 4: poll get_test_results (timeout=${POLL_TIMEOUT}s, run_id=${RUN_ID:-<missing>})"

FINAL_REPORT=""
FINAL_STATUS="unknown"

if [[ -z "$RUN_ID" ]]; then
  step_fail "get_test_results_poll" "skipped — no run_id from step 3"
else
  POLL_OUT=$(uv run python -c "
import asyncio, json, sys, time
sys.path.insert(0, 'src')
import blop.config

from blop.tools.results import get_test_results

run_id = '$RUN_ID'
timeout = float('$POLL_TIMEOUT')
deadline = time.monotonic() + timeout
status = 'unknown'
result = {}

async def poll():
    global status, result
    while time.monotonic() < deadline:
        result = await get_test_results(run_id=run_id)
        status = result.get('status', 'unknown')
        if status in ('completed', 'failed', 'cancelled'):
            break
        await asyncio.sleep(5.0)
    return result

result = asyncio.run(poll())

if hasattr(result, 'model_dump'):
    print(json.dumps(result.model_dump()))
elif isinstance(result, (dict, list)):
    print(json.dumps(result))
else:
    print(str(result))
" 2>&1) || true

  if echo "$POLL_OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    FINAL_STATUS=$(echo "$POLL_OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('status', 'unknown'))
" 2>/dev/null || echo "unknown")
    FINAL_REPORT="$POLL_OUT"

    if [[ "$FINAL_STATUS" == "completed" || "$FINAL_STATUS" == "failed" || "$FINAL_STATUS" == "cancelled" ]]; then
      step_pass "get_test_results_poll"
      echo "    final_status=$FINAL_STATUS"
    else
      step_fail "get_test_results_poll" "timed out after ${POLL_TIMEOUT}s; last status=$FINAL_STATUS"
    fi
  else
    step_fail "get_test_results_poll" "non-JSON output: ${POLL_OUT:0:400}"
  fi
fi

# ---------------------------------------------------------------------------
# Step 5 — assert decision is SHIP/INVESTIGATE/BLOCK
# ---------------------------------------------------------------------------
separator
echo "STEP 5: assert release decision"

if [[ -z "$FINAL_REPORT" ]]; then
  step_fail "release_decision" "skipped — no final report from step 4"
else
  DECISION=$(echo "$FINAL_REPORT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
rec = d.get('release_recommendation') or {}
decision = str(rec.get('decision') or '').upper()
print(decision)
" 2>/dev/null || echo "")

  if [[ "$DECISION" == "SHIP" || "$DECISION" == "INVESTIGATE" || "$DECISION" == "BLOCK" ]]; then
    step_pass "release_decision"
    echo "    decision=$DECISION"
  elif [[ -z "$DECISION" ]]; then
    step_fail "release_decision" "decision field missing from release_recommendation in report"
  else
    step_fail "release_decision" "unexpected decision='$DECISION' (expected SHIP/INVESTIGATE/BLOCK)"
  fi
fi

# ---------------------------------------------------------------------------
# Step 6 — artifact count check (warn if 0, don't fail)
# ---------------------------------------------------------------------------
separator
echo "STEP 6: artifact count check"

if [[ -z "$RUN_ID" ]]; then
  step_fail "artifact_count" "skipped — no run_id from step 3"
else
  ARTIFACT_OUT=$(uv run python -c "
import asyncio, json, sys
sys.path.insert(0, 'src')
import blop.config

from blop.tools.results import get_artifact_index_resource

result = asyncio.run(get_artifact_index_resource('$RUN_ID'))

if hasattr(result, 'model_dump'):
    print(json.dumps(result.model_dump()))
elif isinstance(result, (dict, list)):
    print(json.dumps(result))
else:
    print(str(result))
" 2>&1) || true

  if echo "$ARTIFACT_OUT" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    ARTIFACT_COUNT=$(echo "$ARTIFACT_OUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
# artifacts may be a list under 'artifacts', 'cases', or a count field
artifacts = d.get('artifacts') or []
cases = d.get('cases') or []
count = max(len(artifacts), len(cases))
total = d.get('total_artifacts', count)
print(int(total) if str(total).isdigit() else count)
" 2>/dev/null || echo "0")

    if [[ "$ARTIFACT_COUNT" -ge 1 ]]; then
      step_pass "artifact_count"
      echo "    artifact_count=$ARTIFACT_COUNT"
    else
      # warn but pass: runs without recorded flows produce 0 artifacts
      PASS=$(( PASS + 1 ))
      echo "  WARN  [artifact_count]: 0 artifacts returned (run_id=$RUN_ID) — may be normal if no flows are recorded"
    fi
  else
    step_fail "artifact_count" "non-JSON output: ${ARTIFACT_OUT:0:400}"
  fi
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
separator
echo "=== SMOKE RESULTS: $PASS passed, $FAIL failed ==="

if [[ "${#STEP_ERRORS[@]}" -gt 0 ]]; then
  echo ""
  echo "Failed steps:"
  for err in "${STEP_ERRORS[@]}"; do
    echo "  - $err"
  done
fi

separator

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
