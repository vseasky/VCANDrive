#!/usr/bin/env bash
# Shared stress test for VCAN and VKGS SocketCAN drivers.
#
# Requires two physical CAN ports on the same terminated CAN bus. The test:
#   1. checks mixed Classical CAN at the J1939-standard 250 kbit/s rate;
#   2. simulates concurrent ECU periodic, event, diagnostic and J1939 traffic;
#   3. transfers real ISO-TP diagnostics and J1939 TP.CM/TP.DT messages;
#   4. drives Classical CAN to approximately 90% bus load;
#   5. stresses mixed Classical/extended/RTR frames at 1 Mbit/s;
#   6. stresses mixed CAN/CAN-FD/BRS traffic at 1/5 Mbit/s.
#
# Examples:
#   sudo ../vcan_usb/test_vcan_usb.sh
#   sudo env BUILD=0 DURATION=20 J1939_SIZE=1785 ../vcan_usb/test_vcan_usb.sh
#   sudo env TARGET_LOAD=90 LOAD_TOLERANCE=15 ../vcan_usb/test_vcan_usb.sh
set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
DRIVER=${DRIVER:-vcan_usb}
[[ $DRIVER == vcan_usb || $DRIVER == vkgs_usb ]] || { echo "unsupported DRIVER: $DRIVER"; exit 1; }
DRIVER_DIR=$(cd -- "$SCRIPT_DIR/../../$DRIVER" && pwd)
cd "$DRIVER_DIR" || exit 1

[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

BUILD=${BUILD:-1}
REQUIRE_ALL=${REQUIRE_ALL:-0}
DURATION=${DURATION:-10}
SETTLE=${SETTLE:-1}
TARGET_LOAD=${TARGET_LOAD:-90}
LOAD_TOLERANCE=${LOAD_TOLERANCE:-15}
J1939_SIZE=${J1939_SIZE:-1024}
J1939_TIMEOUT=${J1939_TIMEOUT:-12}
ISOTP_SIZE=${ISOTP_SIZE:-512}
SEQUENCE_FRAMES=${SEQUENCE_FRAMES:-10000}
CANFD_LOOPS=${CANFD_LOOPS:-100}
RX_BUFFER=${RX_BUFFER:-8388608}
DRAIN_TIME=${DRAIN_TIME:-2}
REOPEN_LOOPS=${REOPEN_LOOPS:-5}

FAILED=0
FD_SUPPORTED=1
SKIPPED=0
IFACES=()
PIDS=()
EXTRA_PIDS=()
TMPDIR_TEST=
ERROR_LOG=

hr() { printf '\n=== %s ===\n' "$*"; }
fail() { echo "FAIL: $*"; FAILED=1; }
skip() {
	echo "SKIP: $*"; SKIPPED=$((SKIPPED + 1))
	[[ $REQUIRE_ALL == 0 ]] || fail "REQUIRE_ALL=1: required coverage unavailable"
}
# Phase boundaries stop new load after any failure, including a masked status.
run_phase()
{
	local phase=$1 rc=0 started=$SECONDS
	"$phase" || rc=$?
	(( rc == 0 )) || fail "$phase returned $rc"
	echo "  phase elapsed: $((SECONDS - started))s"
	(( FAILED == 0 ))
}

require_fd()
{
	if [[ ${FD_SUPPORTED:-1} == 0 ]]; then
		skip "device does not advertise CAN FD ($1)"
		return 1
	fi
}

need() {
	command -v "$1" >/dev/null || {
		echo "missing tool: $1 (install can-utils/coreutils)"
		exit 1
	}
}

is_uint() { [[ $1 =~ ^[0-9]+$ ]]; }

validate_settings()
{
	local name value
	[[ $BUILD =~ ^[01]$ && $REQUIRE_ALL =~ ^[01]$ ]] || {
		echo "BUILD and REQUIRE_ALL must be 0 or 1"; exit 1;
	}
	for name in DURATION SETTLE TARGET_LOAD LOAD_TOLERANCE J1939_SIZE J1939_TIMEOUT \
		    ISOTP_SIZE SEQUENCE_FRAMES CANFD_LOOPS RX_BUFFER DRAIN_TIME REOPEN_LOOPS; do
		value=${!name}
		is_uint "$value" || { echo "$name must be a non-negative integer"; exit 1; }
		# Normalize leading zeros before Bash arithmetic (which otherwise uses octal).
		printf -v "$name" '%d' "$((10#$value))"
	done
	(( DURATION > 0 && TARGET_LOAD > 0 && TARGET_LOAD <= 100 )) || {
		echo "DURATION must be > 0 and TARGET_LOAD must be 1..100"
		exit 1
	}
	(( J1939_SIZE > 8 && J1939_SIZE <= 1785 )) || {
		echo "J1939_SIZE must be 9..1785 bytes to exercise J1939 TP, not ETP"
		exit 1
	}
	(( ISOTP_SIZE > 7 && ISOTP_SIZE <= 4095 )) || {
		echo "ISOTP_SIZE must be 8..4095 bytes"
		exit 1
	}
	(( SEQUENCE_FRAMES > 255 && CANFD_LOOPS > 0 )) || {
		echo "SEQUENCE_FRAMES must be > 255 and CANFD_LOOPS must be > 0"
		exit 1
	}
	(( J1939_TIMEOUT > 0 && RX_BUFFER > 0 )) || {
		echo "J1939_TIMEOUT and RX_BUFFER must be > 0"; exit 1;
	}
}

wait_traffic_jobs()
{
	local pid rc
	# PIDS[0] is the error monitor. Every producer must complete successfully.
	for pid in "${PIDS[@]:1}"; do
		rc=0
		wait "$pid" || rc=$?
		[[ $rc == 0 || $rc == 124 ]] || fail "traffic/load process $pid failed (rc=$rc)"
	done
	PIDS=("${PIDS[0]}")
}

stop_jobs()
{
	local pid
	for pid in "${PIDS[@]}" "${EXTRA_PIDS[@]}"; do
		kill -TERM "$pid" 2>/dev/null || true
	done
	for pid in "${PIDS[@]}" "${EXTRA_PIDS[@]}"; do
		wait "$pid" 2>/dev/null || true
	done
	PIDS=()
	EXTRA_PIDS=()
}

cleanup()
{
	local rc=$? iface
	stop_jobs
	for iface in "${IFACES[@]}"; do
		ip link set "$iface" down 2>/dev/null || true
	done
	if [[ -n ${TMPDIR_TEST:-} && -d $TMPDIR_TEST ]]; then
		if (( FAILED || rc != 0 )); then
			echo "diagnostics retained: $TMPDIR_TEST"
		else
			rm -rf -- "$TMPDIR_TEST"
		fi
	fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

detect_ifaces()
{
	local path driver
	IFACES=()
	for path in /sys/class/net/*; do
		[[ -e $path ]] || continue
		driver=$(basename "$(readlink -f "$path/device/driver" 2>/dev/null)" 2>/dev/null)
		[[ $driver == "$DRIVER" ]] && IFACES+=("$(basename "$path")")
	done
	if (( ${#IFACES[@]} )); then
		IFS=$'\n' IFACES=($(printf '%s\n' "${IFACES[@]}" | sort -u)); unset IFS
	fi
}

supports_fd()
{
	local iface=$1 details max_mtu

	# min_mtu/max_mtu are rtnetlink attributes; most kernels do not expose
	# them as /sys/class/net/<iface>/max_mtu files. Newer iproute2 prints the
	# value in the first `ip -details` line.
	details=$(ip -details link show dev "$iface") || return 1
	max_mtu=$(awk '
		{
			for (i = 1; i <= NF; i++)
				if ($i == "maxmtu" && (i + 1) <= NF) {
					print $(i + 1)
					exit
				}
		}' <<< "$details")
	if [[ $max_mtu =~ ^[0-9]+$ ]]; then
		(( max_mtu >= 72 ))
		return
	fi

	# Old iproute2 versions omit maxmtu. Defer the authoritative check to the
	# CAN-FD bring-up phase instead of rejecting a capable interface here.
	echo "  $iface: iproute2 does not report maxmtu; CAN-FD check deferred"
	return 0
}

configure_bus()
{
	local mode=$1 bitrate=$2 dbitrate=${3:-0} iface berr_error
	for iface in "${IFACES[@]}"; do
		ip link set "$iface" down 2>/dev/null || true
		if [[ $mode == fd ]]; then
			ip link set "$iface" type can bitrate "$bitrate" sample-point 0.75 \
				dbitrate "$dbitrate" dsample-point 0.75 fd on \
				loopback off listen-only off restart-ms 100 || {
					echo "  $iface: required CAN-FD timing/configuration failed"
					return 1
				}
		else
			ip link set "$iface" type can bitrate "$bitrate" sample-point 0.75 \
				fd off loopback off listen-only off restart-ms 100 || {
					echo "  $iface: required Classical CAN timing/configuration failed"
					return 1
				}
		fi

		# BERR reporting is firmware-feature-dependent. Keep state/error-frame
		# monitoring active when it is unavailable, but do not reject otherwise
		# valid bitrate configuration in the same netlink transaction.
		if ! berr_error=$(ip link set "$iface" type can berr-reporting on 2>&1); then
			echo "  $iface: could not enable berr-reporting; protocol-error detail reduced"
			printf '    %s\n' "$berr_error"
		fi
		ip link set "$iface" type can termination 120 || {
			echo "  $iface: termination configuration failed"
			return 1
		}
		ip link set "$iface" txqueuelen 4096 || {
			echo "  $iface: txqueuelen configuration failed"
			return 1
		}
		ip link set "$iface" up || {
			echo "  $iface: interface start failed"
			return 1
		}
	done
	sleep "$SETTLE"
}

read_stat()
{
	local iface=$1 stat=$2 value
	# sysfs exposes one integer per file; no external cat process is needed.
	IFS= read -r value < "/sys/class/net/$iface/statistics/$stat" || return 1
	[[ $value =~ ^[0-9]+$ ]] || return 1
	printf '%s\n' "$value"
}

snapshot_stats()
{
	local prefix=$1 iface stat value
	for iface in "${IFACES[@]}"; do
		for stat in rx_packets tx_packets rx_bytes tx_bytes rx_errors tx_errors \
			    rx_dropped tx_dropped rx_length_errors rx_over_errors \
			    rx_crc_errors rx_frame_errors rx_fifo_errors rx_missed_errors \
			    tx_aborted_errors tx_carrier_errors tx_fifo_errors \
			    tx_heartbeat_errors tx_window_errors; do
			value=$(read_stat "$iface" "$stat") || return 1
			printf -v "${prefix}_${iface}_${stat}" '%s' "$value"
		done
	done
}

report_stats()
{
	local prefix=$1 label=$2 iface stat before after delta bad=0
	echo "  $label statistics delta:"
	for iface in "${IFACES[@]}"; do
		printf '  %-8s' "$iface"
		for stat in rx_packets tx_packets rx_bytes tx_bytes; do
			before=${prefix}_${iface}_${stat}; before=${!before}
			after=$(read_stat "$iface" "$stat") || return 1
			printf ' %s=%d' "$stat" "$((after - before))"
		done
		echo
		for stat in rx_errors tx_errors rx_dropped tx_dropped \
			    rx_length_errors rx_over_errors rx_crc_errors rx_frame_errors \
			    rx_fifo_errors rx_missed_errors tx_aborted_errors \
			    tx_carrier_errors tx_fifo_errors tx_heartbeat_errors \
			    tx_window_errors; do
			before=${prefix}_${iface}_${stat}; before=${!before}
			after=$(read_stat "$iface" "$stat") || return 1
			delta=$((after - before))
			if (( delta != 0 )); then
				echo "  $iface: $stat increased by $delta"
				bad=1
			fi
		done
	done
	(( bad == 0 ))
}

check_one_way_totals()
{
	local prefix=$1 tx=$2 rx=$3 label=$4
	local name tx_packets rx_packets tx_bytes rx_bytes value
	name=${prefix}_${tx}_tx_packets
	value=$(read_stat "$tx" tx_packets) || { fail "cannot read tx_packets"; return 1; }
	tx_packets=$((value - ${!name}))
	name=${prefix}_${rx}_rx_packets
	value=$(read_stat "$rx" rx_packets) || { fail "cannot read rx_packets"; return 1; }
	rx_packets=$((value - ${!name}))
	name=${prefix}_${tx}_tx_bytes
	value=$(read_stat "$tx" tx_bytes) || { fail "cannot read tx_bytes"; return 1; }
	tx_bytes=$((value - ${!name}))
	name=${prefix}_${rx}_rx_bytes
	value=$(read_stat "$rx" rx_bytes) || { fail "cannot read rx_bytes"; return 1; }
	rx_bytes=$((value - ${!name}))
	if (( tx_packets <= 0 || tx_packets != rx_packets || tx_bytes != rx_bytes )); then
		fail "$label accounting mismatch: TX ${tx_packets}/${tx_bytes}B, RX ${rx_packets}/${rx_bytes}B"
		return 1
	fi
	echo "  $label accounting: TX == RX (${tx_packets} frames, ${tx_bytes} bytes)"
}

start_error_monitor()
{
	local specs=() iface
	: > "$ERROR_LOG"
	for iface in "${IFACES[@]}"; do
		# Inverted zero filter rejects normal frames; error mask accepts all errors.
		specs+=("$iface,0~0,#FFFFFFFF")
	done
	# -L and human-readable decoding (-e) are incompatible in can-utils.
	# The #FFFFFFFF socket filter enables error reception independently of -e.
	stdbuf -oL candump -L -r "$RX_BUFFER" "${specs[@]}" >> "$ERROR_LOG" 2>&1 &
	PIDS+=("$!")
	sleep 0.2
	if ! kill -0 "${PIDS[0]}" 2>/dev/null; then
		fail "CAN error monitor failed to start"
		sed -n '1,20p' "$ERROR_LOG"
		exit 1
	fi
}

check_error_frames()
{
	local label=$1 count bad=0
	# Hardware transmission, peer RX delivery and sender USB completion are
	# asynchronous. Let both RX and TX completion queues drain before sampling
	# statistics; otherwise a healthy run can differ by a few in-flight frames.
	sleep "$DRAIN_TIME"
	kill -0 "${PIDS[0]}" 2>/dev/null || {
		fail "$label: CAN error monitor exited early"; bad=1;
	}
	stop_jobs
	if grep -qv '^(' "$ERROR_LOG"; then
		fail "$label: CAN error monitor reported a diagnostic"
		sed -n '1,20p' "$ERROR_LOG"
		bad=1
	fi
	count=$(grep -c '^(' "$ERROR_LOG" 2>/dev/null || true)
	if (( count != 0 )); then
		fail "$label emitted $count CAN error frame(s)"
		sed -n '1,20p' "$ERROR_LOG"
		return 1
	fi
	(( bad == 0 )) || return 1
	echo "  error frames: 0"
}

has_cansequence_fd()
{
	# Consume all output: grep -q can cause SIGPIPE with pipefail enabled.
	{ cansequence -h 2>&1 || true; } | grep -- '--canfd' >/dev/null
}

has_canfdtest_fd()
{
	{ canfdtest 2>&1 || true; } | grep -- '-d.*CAN FD' >/dev/null
}

run_cangen_for()
{
	local duration=$1
	shift
	# Called in the background: keep the tracked PID on timeout itself so
	# cleanup signals reach the generator's process group as well.
	exec timeout --signal=INT --kill-after=2 "$duration" cangen "$@"
}

phase_250k_mixed()
{
	local tx=${IFACES[0]} rx=${IFACES[1]} base_rx after_rx
	hr "250 kbit/s Classical CAN: standard + extended + RTR"
	configure_bus classic 250000 || { fail "250 kbit/s bring-up"; return; }
	snapshot_stats P250 || { fail "cannot read baseline statistics"; return 1; }
	base_rx=$(read_stat "$rx" rx_packets)
	start_error_monitor

	# Three persistent generators avoid the fork-per-frame cost of cansend.
	run_cangen_for "$DURATION" "$tx" -g 3 -I r -L r -D i -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$tx" -g 5 -e -I r -L 8 -D r -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$tx" -g 20 -R -I r -L r -i -x & PIDS+=("$!")
	wait_traffic_jobs
	after_rx=$(read_stat "$rx" rx_packets)
	(( after_rx > base_rx )) || fail "250 kbit/s receiver saw no traffic"
	check_error_frames "250 kbit/s mixed traffic" || true
	check_one_way_totals P250 "$tx" "$rx" "250 kbit/s mixed" || true
	report_stats P250 "250 kbit/s mixed" || fail "250 kbit/s statistics contain errors/drops"
}

phase_ecu_profile()
{
	local a=${IFACES[0]} b=${IFACES[1]} a_rx b_rx
	hr "ECU network profile: arbitration + periodic/event/diagnostic traffic"
	configure_bus classic 250000 || { fail "ECU-profile bring-up"; return; }
	snapshot_stats PECU || { fail "cannot read baseline statistics"; return 1; }
	a_rx=$(read_stat "$a" rx_packets)
	b_rx=$(read_stat "$b" rx_packets)
	start_error_monitor

	# ECU A: safety/control frames have lower CAN IDs and win arbitration.
	run_cangen_for "$DURATION" "$a" -g 10  -I 080 -L 8 -D i -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$a" -g 20  -I 180 -L 8 -D i -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$a" -g 100 -I 380 -L 8 -D r -i -x & PIDS+=("$!")
	# J1939 ECU SA=0x80: EEC1, CCVS, DM1, address claim and proprietary PGN.
	run_cangen_for "$DURATION" "$a" -g 10   -e -I 18F00480 -L 8 -D i -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$a" -g 100  -e -I 18FEF180 -L 8 -D r -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$a" -g 1000 -e -I 18FECA80 -L 8 -D 00000000FFFFFFFF -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$a" -g 250  -e -I 18EEFF80 -L 8 -D 0102030405060780 -i -x & PIDS+=("$!")

	# ECU B competes on the same bus: status, diagnostic request, event bursts,
	# J1939 request PGN and occasional Classical RTR polling.
	run_cangen_for "$DURATION" "$b" -g 10  -I 100 -L 8 -D i -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$b" -g 50  -I 500 -L 8 -D r -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$b" -g 200 -e -I 18EA8081 -L 3 -D 04F000 -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$b" -g 25  -e -I 18FF5081 -L 8 -D r -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$b" -g 500 -R -I 600 -L 8 -i -x & PIDS+=("$!")

	wait_traffic_jobs
	(( $(read_stat "$a" rx_packets) > a_rx )) || fail "ECU A received no peer traffic"
	(( $(read_stat "$b" rx_packets) > b_rx )) || fail "ECU B received no peer traffic"
	check_error_frames "ECU network profile" || true
	check_one_way_totals PECU "$a" "$b" "ECU A-to-B" || true
	check_one_way_totals PECU "$b" "$a" "ECU B-to-A" || true
	report_stats PECU "ECU profile" || fail "ECU profile statistics contain errors/drops"
}

wait_for_size()
{
	local file=$1 wanted=$2 timeout_s=$3 ticks=0 size=0
	while (( ticks < timeout_s * 10 )); do
		[[ -e $file ]] && size=$(stat -c %s "$file" 2>/dev/null || echo 0)
		(( size >= wanted )) && return 0
		sleep 0.1
		((ticks++))
	done
	return 1
}

j1939_one_way()
{
	local tx=$1 rx=$2 tx_sa=$3 rx_sa=$4 tag=$5
	local payload="$TMPDIR_TEST/j1939_${tag}.in"
	local output="$TMPDIR_TEST/j1939_${tag}.out"
	local capture="$TMPDIR_TEST/j1939_${tag}.candump"
	local sender_log="$TMPDIR_TEST/j1939_${tag}.sender.log"
	local receiver capture_pid receiver_pid sender_rc=0 payload_ok=0

	dd if=/dev/urandom of="$payload" bs="$J1939_SIZE" count=1 status=none
	: > "$output"
	: > "$capture"
	stdbuf -oL candump -L -r "$RX_BUFFER" "$rx" > "$capture" 2>&1 &
	capture_pid=$!
	EXTRA_PIDS+=("$capture_pid")
	timeout --signal=INT --kill-after=2 "$J1939_TIMEOUT" \
		j1939cat "$rx:$rx_sa,0x0EF00" -r > "$output" 2>/dev/null &
	receiver_pid=$!
	EXTRA_PIDS+=("$receiver_pid")
	sleep 0.3

	timeout --signal=INT --kill-after=2 "$J1939_TIMEOUT" \
		j1939cat -P 1000 -i "$payload" "$tx:$tx_sa" ":$rx_sa,0x0EF00" \
		2> "$sender_log" || sender_rc=$?
	if ! wait_for_size "$output" "$J1939_SIZE" "$J1939_TIMEOUT"; then
		fail "J1939 $tag receiver timed out"
	fi
	kill -INT "$receiver_pid" "$capture_pid" 2>/dev/null || true
	wait "$receiver_pid" "$capture_pid" 2>/dev/null || true
	EXTRA_PIDS=()

	if cmp -s "$payload" "$output"; then
		payload_ok=1
	else
		fail "J1939 $tag payload mismatch ($(stat -c %s "$output")/$J1939_SIZE bytes)"
	fi
	if (( sender_rc != 0 )); then
		if (( payload_ok )); then
			echo "  $tag: j1939cat returned $sender_rc after successful transfer (tool notification)"
			sed -n '1,3p' "$sender_log"
		else
			fail "J1939 $tag sender returned $sender_rc"
		fi
	fi
	local cm dt
	cm=$(grep -Eci '18EC[0-9A-F]{4}' "$capture" || true)
	dt=$(grep -Eci '18EB[0-9A-F]{4}' "$capture" || true)
	echo "  $tag: payload=$J1939_SIZE bytes TP.CM=$cm TP.DT=$dt"
	(( cm > 0 && dt > 0 )) || fail "J1939 $tag did not expose TP.CM/TP.DT on the wire"
}

phase_j1939_tp()
{
	hr "J1939 directed multi-packet TP.CM/TP.DT @ 250 kbit/s"
	configure_bus classic 250000 || { fail "J1939 bring-up"; return; }
	modprobe can-j1939 2>/dev/null || modprobe can_j1939 2>/dev/null || {
		fail "cannot load kernel CAN J1939 support"; return;
	}
	snapshot_stats PJ1939 || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor
	j1939_one_way "${IFACES[0]}" "${IFACES[1]}" 0x80 0x90 A_to_B
	j1939_one_way "${IFACES[1]}" "${IFACES[0]}" 0x90 0x80 B_to_A
	check_error_frames "J1939 TP" || true
	check_one_way_totals PJ1939 "${IFACES[0]}" "${IFACES[1]}" "J1939 A-to-B" || true
	check_one_way_totals PJ1939 "${IFACES[1]}" "${IFACES[0]}" "J1939 B-to-A" || true
	report_stats PJ1939 "J1939 TP" || fail "J1939 statistics contain errors/drops"
}

phase_isotp()
{
	local tx=${IFACES[0]} rx=${IFACES[1]}
	local binary="$TMPDIR_TEST/isotp.bin"
	local input="$TMPDIR_TEST/isotp.in"
	local output="$TMPDIR_TEST/isotp.out"
	local receiver_pid expected actual
	hr "ISO-TP diagnostic multi-frame transfer @ 500 kbit/s"
	configure_bus classic 500000 || { fail "ISO-TP bring-up"; return; }
	modprobe can-isotp 2>/dev/null || modprobe can_isotp 2>/dev/null || {
		fail "cannot load kernel CAN ISO-TP support"; return;
	}
	snapshot_stats PISOTP || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor
	dd if=/dev/urandom of="$binary" bs="$ISOTP_SIZE" count=1 status=none
	od -An -v -tx1 "$binary" > "$input"
	: > "$output"
	timeout --signal=INT --kill-after=2 "$J1939_TIMEOUT" \
		isotprecv -s 7E8 -d 7E0 -b 8 -m 0 "$rx" > "$output" 2>/dev/null &
	receiver_pid=$!
	EXTRA_PIDS+=("$receiver_pid")
	sleep 0.3
	if ! timeout --signal=INT --kill-after=2 "$J1939_TIMEOUT" \
		isotpsend -s 7E0 -d 7E8 -b "$tx" < "$input"; then
		fail "ISO-TP sender failed"
	fi
	wait "$receiver_pid" 2>/dev/null || fail "ISO-TP receiver timed out"
	EXTRA_PIDS=()
	expected=$(tr -d '[:space:]' < "$input" | tr '[:lower:]' '[:upper:]')
	actual=$(tr -d '[:space:]' < "$output" | tr '[:lower:]' '[:upper:]')
	if [[ $actual != "$expected" ]]; then
		fail "ISO-TP payload mismatch (expected ${#expected} hex chars, got ${#actual})"
	fi
	echo "  diagnostic payload: $ISOTP_SIZE bytes, IDs 0x7E0/0x7E8"
	check_error_frames "ISO-TP diagnostics" || true
	check_one_way_totals PISOTP "$tx" "$rx" "ISO-TP requests" || true
	check_one_way_totals PISOTP "$rx" "$tx" "ISO-TP flow control" || true
	report_stats PISOTP "ISO-TP" || fail "ISO-TP statistics contain errors/drops"
}

sequence_one_way()
{
	local mode=$1 tx=$2 rx=$3 can_id=$4 tag=$5
	local rx_log="$TMPDIR_TEST/sequence_${tag}.rx"
	local tx_log="$TMPDIR_TEST/sequence_${tag}.tx"
	local opts=() receiver_pid rx_rc=0 tx_rc=0 timeout_s
	[[ $mode == fd ]] && opts=(-f -s -b -e)
	timeout_s=$((DURATION + 20))

	timeout --signal=INT --kill-after=2 "$timeout_s" \
		cansequence "$rx" "${opts[@]}" -i "$can_id" -r \
		--loop "$SEQUENCE_FRAMES" -q1 > "$rx_log" 2>&1 &
	receiver_pid=$!
	EXTRA_PIDS+=("$receiver_pid")
	sleep 0.2
	timeout --signal=INT --kill-after=2 "$timeout_s" \
		cansequence "$tx" "${opts[@]}" -i "$can_id" -p \
		--loop "$SEQUENCE_FRAMES" > "$tx_log" 2>&1 || tx_rc=$?
	wait "$receiver_pid" || rx_rc=$?
	EXTRA_PIDS=()
	if (( tx_rc != 0 || rx_rc != 0 )); then
		fail "$tag sequence integrity failed (sender=$tx_rc receiver=$rx_rc)"
		sed -n '1,12p' "$rx_log"
		return 1
	fi
	echo "  $tag: $SEQUENCE_FRAMES ordered frames, no gap/reorder/socket overflow"
}

phase_sequence_integrity()
{
	hr "Sequence integrity: distinguish driver loss from socket overflow"
	configure_bus classic 500000 || { fail "Classical sequence bring-up"; return; }
	snapshot_stats PSEQCC || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor
	sequence_one_way classic "${IFACES[0]}" "${IFACES[1]}" 0x321 classic_A_to_B
	check_error_frames "Classical sequence" || true
	check_one_way_totals PSEQCC "${IFACES[0]}" "${IFACES[1]}" "Classical sequence" || true
	report_stats PSEQCC "Classical sequence" || fail "Classical sequence statistics contain errors/drops"

	(( FAILED == 0 )) || return 1
	require_fd "sequence" || return 0
	if ! has_cansequence_fd; then
		skip "installed cansequence has no CAN-FD support; upgrade can-utils"
		return
	fi
	configure_bus fd 1000000 5000000 || { fail "CAN-FD sequence bring-up"; return; }
	snapshot_stats PSEQFD || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor
	sequence_one_way fd "${IFACES[1]}" "${IFACES[0]}" 0x1ABCDE0 fd_B_to_A
	check_error_frames "CAN-FD sequence" || true
	check_one_way_totals PSEQFD "${IFACES[1]}" "${IFACES[0]}" "CAN-FD sequence" || true
	report_stats PSEQFD "CAN-FD sequence" || fail "CAN-FD sequence statistics contain errors/drops"
}

phase_canfd_roundtrip()
{
	require_fd "roundtrip" || return 0
	local dut=${IFACES[1]} host=${IFACES[0]}
	local dut_log="$TMPDIR_TEST/canfdtest.dut"
	local host_log="$TMPDIR_TEST/canfdtest.host"
	local dut_pid host_rc=0
	hr "CAN-FD bidirectional in-flight ordering and payload integrity"
	if ! has_canfdtest_fd; then
		skip "installed canfdtest has no CAN-FD options; upgrade can-utils"
		return
	fi
	configure_bus fd 1000000 5000000 || { fail "canfdtest bring-up"; return; }
	snapshot_stats PFDTEST || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor
	timeout --signal=INT --kill-after=2 60 \
		canfdtest -d -b -x -v "$dut" > "$dut_log" 2>&1 &
	dut_pid=$!
	EXTRA_PIDS+=("$dut_pid")
	sleep 0.3
	timeout --signal=INT --kill-after=2 60 \
		canfdtest -d -b -x -g -v -l "$CANFD_LOOPS" -f 50 "$host" \
		> "$host_log" 2>&1 || host_rc=$?
	kill -INT "$dut_pid" 2>/dev/null || true
	wait "$dut_pid" 2>/dev/null || true
	EXTRA_PIDS=()
	if (( host_rc != 0 )); then
		fail "canfdtest failed (rc=$host_rc)"
		sed -n '1,20p' "$host_log"
	else
		echo "  $CANFD_LOOPS loops x 50 in-flight frames passed"
	fi
	check_error_frames "CAN-FD roundtrip" || true
	check_one_way_totals PFDTEST "$host" "$dut" "CAN-FD host-to-DUT" || true
	check_one_way_totals PFDTEST "$dut" "$host" "CAN-FD DUT-to-host" || true
	report_stats PFDTEST "CAN-FD roundtrip" || fail "CAN-FD roundtrip statistics contain errors/drops"
}

phase_90_load()
{
	local tx=${IFACES[0]} rx=${IFACES[1]}
	local load_log="$TMPDIR_TEST/canbusload.log" burst measured lower upper
	hr "250 kbit/s sustained bus load target: ${TARGET_LOAD}%"
	configure_bus classic 250000 || { fail "load-test bring-up"; return; }
	snapshot_stats PLOAD || { fail "cannot read baseline statistics"; return 1; }
	start_error_monitor

	# An 8-byte Classical frame occupies roughly 130 bits including overhead and
	# worst-case stuffing. At 250 kbit/s, a 10 ms window holds ~19 frames; scale
	# the burst by the requested utilization. canbusload supplies the observation.
	burst=$(( (TARGET_LOAD * 19 + 99) / 100 ))
	(( burst < 1 )) && burst=1
	stdbuf -oL timeout --signal=INT --kill-after=2 "$DURATION" \
		canbusload -e "$rx@250000" > "$load_log" 2>&1 & PIDS+=("$!")
	run_cangen_for "$DURATION" "$tx" -g 10 -c "$burst" -I i -L 8 -D i -i -x & PIDS+=("$!")
	wait_traffic_jobs
	check_error_frames "90% load" || true
	check_one_way_totals PLOAD "$tx" "$rx" "load test" || true
	report_stats PLOAD "load test" || fail "load-test statistics contain errors/drops"

	measured=$(grep -Eo '[0-9]+%' "$load_log" | tr -d '%' | sort -n | tail -1)
	if [[ -z $measured ]]; then
		fail "canbusload produced no load sample"
		return
	fi
	lower=$((TARGET_LOAD - LOAD_TOLERANCE)); (( lower < 0 )) && lower=0
	# canbusload can legitimately estimate slightly above 100% due to stuffing.
	upper=$((TARGET_LOAD + LOAD_TOLERANCE))
	echo "  requested=${TARGET_LOAD}% observed_peak=${measured}% accepted=${lower}..${upper}% burst=$burst/10ms"
	(( measured >= lower && measured <= upper )) || \
		fail "observed load ${measured}% is outside ${lower}..${upper}%"
}

phase_1m_mixed()
{
	local tx=${IFACES[1]} rx=${IFACES[0]} base_rx after_rx
	hr "1 Mbit/s Classical CAN mixed-frame saturation"
	configure_bus classic 1000000 || { fail "1 Mbit/s bring-up"; return; }
	snapshot_stats P1M || { fail "cannot read baseline statistics"; return 1; }
	base_rx=$(read_stat "$rx" rx_packets)
	start_error_monitor
	run_cangen_for "$DURATION" "$tx" -g 0 -p 10 -I r -L r -D r -i -x & PIDS+=("$!")
	run_cangen_for "$DURATION" "$tx" -g 1 -e -R -I r -L r -i -x & PIDS+=("$!")
	wait_traffic_jobs
	after_rx=$(read_stat "$rx" rx_packets)
	(( after_rx > base_rx )) || fail "1 Mbit/s receiver saw no traffic"
	check_error_frames "1 Mbit/s mixed saturation" || true
	check_one_way_totals P1M "$tx" "$rx" "1 Mbit/s saturation" || true
	report_stats P1M "1 Mbit/s saturation" || fail "1 Mbit/s statistics contain errors/drops"
}

phase_fd_stress()
{
	require_fd "mixed stress" || return 0
	local tx=${IFACES[0]} rx=${IFACES[1]} base_rx after_rx
	hr "CAN-FD mixed stress @ 1 Mbit/s nominal / 5 Mbit/s data"
	configure_bus fd 1000000 5000000 || { fail "CAN-FD bring-up"; return; }
	snapshot_stats PFD || { fail "cannot read baseline statistics"; return 1; }
	base_rx=$(read_stat "$rx" rx_packets)
	start_error_monitor
	# -m mixes SFF/EFF, Classic/FD, RTR, BRS and ESI combinations.
	run_cangen_for "$DURATION" "$tx" -g 0 -p 10 -m -I r -L r -D r -i -x & PIDS+=("$!")
	wait_traffic_jobs
	after_rx=$(read_stat "$rx" rx_packets)
	(( after_rx > base_rx )) || fail "CAN-FD receiver saw no traffic"
	check_error_frames "CAN-FD mixed stress" || true
	check_one_way_totals PFD "$tx" "$rx" "CAN-FD stress" || true
	report_stats PFD "CAN-FD stress" || fail "CAN-FD statistics contain errors/drops"
}

phase_reopen()
{
	local round mode tx=${IFACES[0]} rx=${IFACES[1]}
	hr "Repeated close/open and Classical/CAN-FD switching ($REOPEN_LOOPS rounds)"
	for ((round=0; round<REOPEN_LOOPS; round++)); do
		mode=classic
		(( round % 2 && FD_SUPPORTED )) && mode=fd
		configure_bus "$mode" 1000000 5000000 || { fail "reopen round $round"; return; }
		snapshot_stats PREOPEN || { fail "cannot read baseline statistics"; return 1; }
		start_error_monitor
		local opts=()
		[[ $mode == fd ]] && opts=(-f -b)
		timeout --signal=INT --kill-after=2 10 cangen "$tx" "${opts[@]}" \
			-n 100 -g 1 -p 100 -I 321 -L 8 -D i -x || fail "reopen sender round $round"
		check_error_frames "reopen round $round" || true
		check_one_way_totals PREOPEN "$tx" "$rx" "reopen $mode round $round" || true
		report_stats PREOPEN "reopen round $round" || fail "reopen statistics"
		(( FAILED == 0 )) || return 1
	done
}

validate_settings
for tool in ip make modprobe insmod rmmod cangen candump canbusload j1939cat \
	    isotpsend isotprecv cansequence canfdtest timeout stdbuf dd od cmp grep \
	    sort awk; do
	need "$tool"
done

TMPDIR_TEST=$(mktemp -d "/tmp/${DRIVER}.XXXXXX") || exit 1
ERROR_LOG="$TMPDIR_TEST/errors.log"

if [[ $BUILD == 1 ]]; then
	hr "build + reload $DRIVER"
	make >/dev/null || { echo "build failed"; exit 1; }
	if [[ $DRIVER == vkgs_usb ]]; then
		modprobe -r gs_usb 2>/dev/null || true
	fi
	modprobe can-dev || { echo "failed to load can-dev"; exit 1; }
	rmmod "$DRIVER" 2>/dev/null || true
	insmod "./${DRIVER}.ko" || { echo "insmod failed"; exit 1; }
	sleep 2
fi

hr "detect two physical interfaces"
detect_ifaces
(( ${#IFACES[@]} >= 2 )) || {
	echo "need two interfaces bound to $DRIVER; found ${#IFACES[@]}"
	exit 1
}
IFACES=("${IFACES[0]}" "${IFACES[1]}")
for iface in "${IFACES[@]}"; do
	supports_fd "$iface" || { echo "$iface does not advertise CAN-FD MTU"; FD_SUPPORTED=0; }
	echo "  $iface -> $(basename "$(readlink -f "/sys/class/net/$iface/device")")"
done
echo "  wiring required: CANH-CANH, CANL-CANL; termination is enabled on both ports"

suite_started=$SECONDS
for phase in phase_250k_mixed phase_ecu_profile phase_isotp phase_j1939_tp \
             phase_sequence_integrity phase_canfd_roundtrip phase_90_load \
             phase_1m_mixed phase_fd_stress phase_reopen; do
	run_phase "$phase" || break
done
echo "suite elapsed: $((SECONDS - suite_started))s"

hr "result"
echo "skipped optional tests: $SKIPPED"
if (( FAILED )); then
	echo "STRESS TEST FAILED"
	exit 1
fi
if (( SKIPPED )); then
	echo "STRESS TEST PASSED (partial coverage: $SKIPPED optional tests skipped)"
else
	echo "STRESS TEST PASSED"
fi
exit 0
