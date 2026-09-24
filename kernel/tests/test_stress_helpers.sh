#!/usr/bin/env bash
# Hardware-free regression tests for the shared stress-suite failure checks.
set -euo pipefail
TEST_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
# Load only functions, without module loading, root checks or interface changes.
source <(sed -n '/^hr()/,/^validate_settings$/p' "$TEST_DIR/common/stress.sh" |
  sed '/^trap /d; /^validate_settings$/d')
TMPDIR_TEST=$(mktemp -d)
PIDS=()
EXTRA_PIDS=()
trap 'stop_jobs; rm -rf -- "$TMPDIR_TEST"' EXIT
ERROR_LOG="$TMPDIR_TEST/errors.log"
DRAIN_TIME=0
FAILED=0
SKIPPED=0
REQUIRE_ALL=0

# Exercise the actual startup function, including its option combination.
# stdbuf runs in the monitor subprocess; exec keeps its PID trackable.
stdbuf() {
  [[ $1 == -oL && $2 == candump && $3 == -L && $4 == -r &&
     $5 == 131072 && $6 == 'any,0~0,#FFFFFFFF' && $# == 6 ]] || return 1
  exec sleep 30
}
IFACES=(any)
RX_BUFFER=131072
start_error_monitor
check_error_frames startup
[[ $FAILED == 0 ]]
unset -f stdbuf

# Optional read-only integration check with the installed candump binary.
# Use a small buffer so an unprivileged smoke test needs no rmem_max override.
if [[ ${LIVE_MONITOR:-0} == 1 ]]; then
  start_error_monitor
  check_error_frames live_startup
  [[ $FAILED == 0 ]]
fi

# Startup failure must abort before starting traffic.
if (
  stdbuf() { echo "invalid monitor options"; return 1; }
  start_error_monitor
  exit 99
); then
  echo "failed monitor unexpectedly started"; exit 1
else
  [[ $? == 1 ]]
fi

# A failed producer must be noticed even if another producer succeeded.
sleep 30 & PIDS+=("$!")
(exit 0) & PIDS+=("$!")
(exit 7) & PIDS+=("$!")
wait_traffic_jobs
[[ $FAILED == 1 && ${#PIDS[@]} == 1 ]]
stop_jobs

# A live, silent monitor is healthy; stderr output must fail the phase.
FAILED=0
: > "$ERROR_LOG"
sleep 30 & PIDS+=("$!")
check_error_frames healthy
[[ $FAILED == 0 ]]
FAILED=0
printf '(1.000000) can0 20000040#0000000000000000\n' > "$ERROR_LOG"
sleep 30 & PIDS+=("$!")
check_error_frames can_error && exit 1
[[ $FAILED == 1 ]]
FAILED=0
printf 'socket: No buffer space available\n' > "$ERROR_LOG"
sleep 30 & PIDS+=("$!")
check_error_frames diagnostic && exit 1
[[ $FAILED == 1 ]]

# A dead monitor must fail even with an empty capture.
FAILED=0
: > "$ERROR_LOG"
(exit 0) & PIDS+=("$!")
wait "${PIDS[0]}"
check_error_frames dead && exit 1
[[ $FAILED == 1 ]]

# Matching zero TX/RX counters are not a passing traffic test.
read_stat() { printf '0\n'; }
P_a_tx_packets=0 P_b_rx_packets=0 P_a_tx_bytes=0 P_b_rx_bytes=0
FAILED=0
check_one_way_totals P a b empty && exit 1
[[ $FAILED == 1 ]]

FAILED=0
skip optional
[[ $FAILED == 0 && $SKIPPED == 1 ]]
REQUIRE_ALL=1
skip required
[[ $FAILED == 1 && $SKIPPED == 2 ]]

# Leading-zero decimal values must not become Bash octal expressions.
BUILD=0 DURATION=08 SETTLE=0 TARGET_LOAD=90 LOAD_TOLERANCE=15
J1939_SIZE=1785 J1939_TIMEOUT=12 ISOTP_SIZE=4095 SEQUENCE_FRAMES=1000
CANFD_LOOPS=100 RX_BUFFER=8388608 REOPEN_LOOPS=5
validate_settings
[[ $DURATION == 8 ]]

# Usage output may advertise support while the tool exits with status 1.
cansequence() { echo "--canfd"; return 1; }
canfdtest() { echo "-d CAN FD"; return 1; }
has_cansequence_fd
has_canfdtest_fd
cansequence() { echo "--extended"; return 0; }
canfdtest() { echo "-g generate"; return 1; }
has_cansequence_fd && exit 1
has_canfdtest_fd && exit 1
echo "stress helper regression tests passed"

# Failure flags must survive a phase that itself returns success.
FAILED=0
masked_failure() { fail "injected phase failure"; return 0; }
if run_phase masked_failure; then
  echo "masked phase failure passed"; exit 1
fi
[[ $FAILED == 1 ]]
FAILED=0
bad_exit() { return 7; }
if run_phase bad_exit; then
  echo "nonzero phase passed"; exit 1
fi
[[ $FAILED == 1 ]]
FAILED=0
read_stat() { return 1; }
if snapshot_stats P; then
  echo "missing baseline passed"; exit 1
fi
echo "phase boundary and missing-stat regressions passed"

# Device capability skips remain visible, and strict mode still fails.
FD_SUPPORTED=0 FAILED=0 SKIPPED=0 REQUIRE_ALL=0
if require_fd fixture; then exit 1; fi
[[ $SKIPPED == 1 && $FAILED == 0 ]]
REQUIRE_ALL=1
if require_fd fixture; then exit 1; fi
[[ $SKIPPED == 2 && $FAILED == 1 ]]
FD_SUPPORTED=1
require_fd fixture
echo "FD capability regressions passed"
