#!/usr/bin/env bash
set -euo pipefail
TEST_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export DRIVER=vcan_usb
exec bash "$TEST_DIR/../common/stress.sh" "$@"
