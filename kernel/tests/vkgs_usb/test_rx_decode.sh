#!/usr/bin/env bash
# Compile the current RX handler with userspace allocation/delivery stubs.
set -euo pipefail
TEST_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
DRIVER_DIR="$TEST_DIR/../../vkgs_usb"
TEST_TMP=$(mktemp -d)
trap 'rm -rf -- "$TEST_TMP"' EXIT
{
  sed '/^\/\* INSERT_DRIVER \*\//,$d' "$TEST_DIR/rx_decode_fixture.c"
  # Reuse protocol constants and layout from the production header.
  sed -n '/^#define VKGS_USB_FLAG_/p; /^#define VKGS_USB_CAN_FLAG_/p;
    /^struct vkgs_usb_host_frame {/,/^} __packed;/p' "$DRIVER_DIR/vkgs_usb.h"
  sed -n '/^static void vkgs_usb_handle_rx_frame(/,/^}/p' "$DRIVER_DIR/vkgs_usb.c"
  sed -n '/^int main(void)/,$p' "$TEST_DIR/rx_decode_fixture.c"
} | "${CC:-cc}" -x c -std=gnu11 -Wall -Wextra -Werror -O2 -o "$TEST_TMP/rx_decode" -
"$TEST_TMP/rx_decode"
