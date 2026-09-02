#!/usr/bin/env bash
# vkgs_usb functional test.
#
# Builds + reloads the driver, auto-detects every SocketCAN interface bound to
# vkgs_usb (no fixed channel count — works for 1/2/3/4+ independent channels),
# and verifies traffic in six phases (60 frames from each channel by default):
#
#   * Classic CAN @ 1 Mbit/s
#   * CAN FD      @ 1 Mbit/s nominal / 5 Mbit/s data, BRS off
#   * CAN FD      @ 1 Mbit/s nominal / 5 Mbit/s data, BRS on
# First both channels exchange traffic on the shared bus, then both run
# independent internal-loopback tests with the same three frame formats.
#
# NOTE: the firmware reuses the candleLight PID (1d50:606f), so the in-tree
# gs_usb driver is unloaded first; re-modprobe it after testing if you need it.
#
#   sudo bash test_vkgs_usb.sh
#   sudo env NFRAMES=100 bash test_vkgs_usb.sh
#   sudo env BUILD=0 bash test_vkgs_usb.sh    # skip rebuild/reload
#
# This script lives under kernel/tests/, separate from the DKMS-packaged
# driver source under kernel/vkgs_usb/; it cd's into the driver directory to
# build/insmod there.
set -uo pipefail
cd "$(dirname "$0")/../../vkgs_usb"
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)"; exit 1; }

DRIVER=vkgs_usb
NFRAMES="${NFRAMES:-60}"
GAP="${GAP:-0.02}"
SETTLE="${SETTLE:-1}"
BUILD="${BUILD:-1}"
LOG=/tmp/${DRIVER}_test.log
FAILED=0
IFACES=()
hr(){ printf '\n=== %s ===\n' "$*"; }

cleanup(){
  local c
  for c in "${IFACES[@]}"; do ip link set "$c" down 2>/dev/null || true; done
  rm -f "$LOG"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

need(){ command -v "$1" >/dev/null || { echo "missing tool: $1 (install can-utils)"; exit 1; }; }
need cansend; need candump; need ip

count_frames(){ # $1=receiver $2=id $3=classic|fd $4=brs
  awk -v r="$1" -v id="$2" -v kind="$3" -v brs="$4" '
    $2 != r { next }
    kind == "classic" && index($3, id "#") == 1 && index($3, id "##") != 1 {
      count++
      next
    }
    kind == "fd" && index($3, id "##") == 1 {
      # The flags nibble contains BRS (bit 0), ESI (bit 1) and, on newer
      # kernels/can-utils, FDF (bit 2).  Match only the requested BRS state;
      # valid frames are commonly printed as ##4/##5 rather than ##0/##1.
      flag = index("0123456789ABCDEF", toupper(substr($3, length(id) + 3, 1))) - 1
      if (flag >= 0 && (flag % 2) == brs)
        count++
    }
    END { print count + 0 }
  ' "$LOG"
}

build_data(){
  local n=$1 S=$2 seq=$3 out="" k
  for ((k=0; k<n; k++)); do
    if (( k % 2 == 0 )); then out+=$(printf '%02X' $(( S & 0xff )))
    else out+=$(printf '%02X' $(( (seq + k) & 0xff )) ); fi
  done
  printf '%s' "$out"
}

detect_ifaces(){
  IFACES=()
  local n d
  for n in /sys/class/net/can* /sys/class/net/*; do
    [[ -e "$n" ]] || continue
    d=$(basename "$(readlink -f "$n/device/driver" 2>/dev/null)" 2>/dev/null)
    [[ "$d" == "$DRIVER" ]] && IFACES+=("$(basename "$n")")
  done
  # de-dup + sort (guard empty array so no phantom "" element is created)
  if (( ${#IFACES[@]} )); then
    IFS=$'\n' IFACES=($(printf '%s\n' "${IFACES[@]}" | sort -u)); unset IFS
  fi
}

# FD-capable iff the netdev's max MTU is the CAN FD MTU (72) or larger.
iface_supports_fd(){
  local m
  m=$(ip -details link show "$1" 2>/dev/null | grep -oE 'maxmtu [0-9]+' | awk '{print $2; exit}')
  [[ -n "$m" && "$m" -ge 72 ]]
}

bringup(){ # $1=iface $2=classic|fd $3=loopback(on|off)
  local c=$1
  ip link set "$c" down 2>/dev/null || true
  if [[ "$2" == fd ]]; then
    ip link set "$c" type can bitrate 1000000 sample-point 0.75 \
       dbitrate 5000000 dsample-point 0.75 fd on loopback "$3" || return 1
  else
    ip link set "$c" type can bitrate 1000000 sample-point 0.75 \
       loopback "$3" || return 1
  fi
  ip link set "$c" type can termination 120 2>/dev/null || return 1
  ip link set "$c" up || return 1
}

phase(){ # $1=classic|fd $2=brs(0|1) $3=label
  hr "$3"
  local kind=$1 brs=$2 i S R id n cnt ok=1
  for i in "${!IFACES[@]}"; do
    bringup "${IFACES[$i]}" "$kind" off || {
      echo "  bring-up failed for ${IFACES[$i]} ($kind)"; return 1; }
    printf '  %-6s ' "${IFACES[$i]}"
    ip -details link show "${IFACES[$i]}" | awk '/bitrate/&&!/dbitrate/{print "  "$2" "$3" "$4}' | tr -s ' '
  done
  echo "  settling ${SETTLE}s..."; sleep "$SETTLE"

  : > "$LOG"
  stdbuf -oL candump -L "${IFACES[@]}" > "$LOG" 2>/dev/null & local cap=$!
  sleep 0.3

  for S in "${!IFACES[@]}"; do
    id=$(printf '%03X' $((0x100 + S)))
    for ((n=0; n<NFRAMES; n++)); do
      if [[ "$kind" == fd ]]; then
        if ! cansend "${IFACES[$S]}" "${id}##${brs}$(build_data 64 "$S" "$n")"; then
          echo "  TX ERROR: ${IFACES[$S]} FD/BRS=$brs sequence=$n"
          ok=0
          break
        fi
      else
        if ! cansend "${IFACES[$S]}" "${id}#$(build_data 8 "$S" "$n")"; then
          echo "  TX ERROR: ${IFACES[$S]} classic sequence=$n"
          ok=0
          break
        fi
      fi
      sleep "$GAP"
    done
    sleep 0.2
  done
  sleep 0.5; kill "$cap" 2>/dev/null; wait "$cap" 2>/dev/null

  echo
  echo "  RX matrix (rows=receiver, cols=sender; sent $NFRAMES/cell, *=own echo)"
  printf '          '; for S in "${!IFACES[@]}"; do printf ' %6s' "${IFACES[$S]}"; done; echo
  for R in "${!IFACES[@]}"; do
    printf '  %-6s ' "${IFACES[$R]}"
    for S in "${!IFACES[@]}"; do
      id=$(printf '%03X' $((0x100 + S)))
      cnt=$(count_frames "${IFACES[$R]}" "$id" "$kind" "$brs")
      if (( R == S )); then printf '%6s*' "$cnt"; else printf '%7s' "$cnt"; fi
    done; echo
  done

  if (( ${#IFACES[@]} >= 2 )); then
    for R in "${!IFACES[@]}"; do for S in "${!IFACES[@]}"; do
      (( R == S )) && continue
      id=$(printf '%03X' $((0x100 + S)))
      cnt=$(count_frames "${IFACES[$R]}" "$id" "$kind" "$brs")
      (( cnt != NFRAMES )) && { echo "  MISMATCH: ${IFACES[$R]} received $cnt/$NFRAMES from ${IFACES[$S]}"; ok=0; }
    done; done
    (( ok )) && echo "  PHASE OK: every channel received every other channel (bus wired)." \
             || echo "  PHASE INCOMPLETE: are all ports wired to one bus? (CANH-CANH/CANL-CANL)"
  fi
  rm -f "$LOG"
  (( ok ))
}

phase_loopback(){ # $1=classic|fd $2=brs(0|1) $3=label
  hr "$3 (independent internal loopback)"
  local kind=$1 brs=$2 i id n cnt expected ok=1
  for i in "${!IFACES[@]}"; do
    bringup "${IFACES[$i]}" "$kind" on || { echo "  bring-up failed ${IFACES[$i]}"; return 1; }
  done
  sleep "$SETTLE"
  : > "$LOG"; stdbuf -oL candump -L "${IFACES[@]}" > "$LOG" 2>/dev/null & local cap=$!
  sleep 0.3
  for i in "${!IFACES[@]}"; do
    id=$(printf '%03X' $((0x100 + i)))
    for ((n=0; n<NFRAMES; n++)); do
      if [[ "$kind" == fd ]]; then
        if ! cansend "${IFACES[$i]}" "${id}##${brs}$(build_data 64 "$i" "$n")"; then
          echo "  TX ERROR: ${IFACES[$i]} FD/BRS=$brs sequence=$n"
          ok=0
          break
        fi
      else
        if ! cansend "${IFACES[$i]}" "${id}#$(build_data 8 "$i" "$n")"; then
          echo "  TX ERROR: ${IFACES[$i]} classic sequence=$n"
          ok=0
          break
        fi
      fi
      sleep "$GAP"
    done
  done
  sleep 0.5; kill "$cap" 2>/dev/null; wait "$cap" 2>/dev/null
  for i in "${!IFACES[@]}"; do
    id=$(printf '%03X' $((0x100 + i)))
    cnt=$(count_frames "${IFACES[$i]}" "$id" "$kind" "$brs")
    # IFF_ECHO contributes one local TX completion and firmware internal
    # loopback contributes one RX frame, so candump observes two per send.
    expected=$((2 * NFRAMES))
    printf '  %-6s loopback events: %d/%d (TX echo + loopback RX)\n' \
      "${IFACES[$i]}" "$cnt" "$expected"
    (( cnt != expected )) && ok=0
  done
  (( ok )) && echo "  PHASE OK: every channel looped its own frames back." \
           || echo "  PHASE FAIL: missing or unexpected loopback events."
  rm -f "$LOG"
  (( ok ))
}

# ---- build + load -------------------------------------------------------
if [[ "$BUILD" == 1 ]]; then
  hr "build + reload $DRIVER (unloading in-tree gs_usb)"
  make >/dev/null || { echo "build failed"; exit 1; }
  modprobe -r gs_usb 2>/dev/null || true
  rmmod "$DRIVER" 2>/dev/null || true
  # insmod does not resolve module dependencies.  The out-of-tree driver uses
  # symbols exported by can-dev, so load the SocketCAN core explicitly first.
  modprobe can-dev || { echo "failed to load can-dev dependency"; exit 1; }
  insmod "./${DRIVER}.ko" || { echo "insmod failed"; exit 1; }
  sleep 2
fi

hr "detect interfaces bound to $DRIVER"
detect_ifaces
if (( ${#IFACES[@]} == 0 )); then
  echo "no $DRIVER interfaces found. Plugged in? gs_usb still bound? (modprobe -r gs_usb)"
  exit 1
fi
for i in "${!IFACES[@]}"; do
  echo "  ${IFACES[$i]} -> $(basename "$(readlink -f /sys/class/net/${IFACES[$i]}/device)")"
done
(( ${#IFACES[@]} >= 2 )) || { echo "dual-CAN test requires two interfaces"; exit 1; }
IFACES=("${IFACES[0]}" "${IFACES[1]}")
echo "  test mode: dual-CAN bus + independent loopback"

hr "enable 120 ohm termination"
for c in "${IFACES[@]}"; do
  ip link set "$c" down 2>/dev/null || true
  ip link set "$c" type can termination 120 \
    && echo "  $c: 120 ohm enabled" \
    || { echo "  $c: failed to enable termination"; exit 1; }
done

iface_supports_fd "${IFACES[0]}" || { echo "CAN FD is required"; exit 1; }
phase classic 0 "BUS 1: classic CAN @ 1 Mbit/s" || FAILED=1
phase fd 0 "BUS 2: CAN FD @ 1 Mbit/s / 5 Mbit/s, BRS off" || FAILED=1
phase fd 1 "BUS 3: CAN FD @ 1 Mbit/s / 5 Mbit/s, BRS on" || FAILED=1
phase_loopback classic 0 "LOOPBACK 1: classic CAN @ 1 Mbit/s" || FAILED=1
phase_loopback fd 0 "LOOPBACK 2: CAN FD @ 1 Mbit/s / 5 Mbit/s, BRS off" || FAILED=1
phase_loopback fd 1 "LOOPBACK 3: CAN FD @ 1 Mbit/s / 5 Mbit/s, BRS on" || FAILED=1

hr "done"
cleanup
trap - EXIT
echo "all channels set down."
exit "$FAILED"
