/* SPDX-License-Identifier: GPL-2.0 */
/*
 * VCAN USB protocol definitions for the Linux SocketCAN driver.
 *
 * Mirrors the on-wire protocol of the HPMicro VCAN firmware
 * (ref/firmware/vcan_0_0_2). VCAN frames are self-describing: every frame (data and
 * control) starts with a common header { echo_id, opcode, flags }, where the
 * opcode encodes (channel << 12) | byte_size. The device exposes one USB
 * interface per CAN channel, each with its own bulk endpoint pair.
 *
 * Keep this header self-contained for out-of-tree / DKMS builds.
 */

#ifndef _VCAN_USB_H
#define _VCAN_USB_H

#include <linux/types.h>

#define VCAN_USB_DRIVER_NAME "vcan_usb"

#define VCAN_USB_VID         0x1d50
#define VCAN_USB_PID         0x6080

#define VCAN_USB_CLOCK_HZ    80000000U
#define VCAN_USB_MAX_XFER    512U

/* Bulk IN/OUT endpoints are discovered per interface from the USB descriptor
 * (usb_find_common_endpoints); the driver never hard-codes endpoint addresses,
 * so any number of independent channels/interfaces is supported.
 */

/* bRequest: low 7 bits = request type, bit 7 = device->host (read) direction. */
#define VCAN_USB_BREQ_DIR_READ  0x80
#define VCAN_USB_BREQ_BASE_MASK 0x7f

enum vcan_usb_breq {
	VCAN_USB_BREQ_HOST_FORMAT    = 0,
	VCAN_USB_BREQ_MODE           = 1,
	VCAN_USB_BREQ_BERR           = 2,
	VCAN_USB_BREQ_CAN_STATE      = 3,
	VCAN_USB_BREQ_BT_CONST       = 16,
	VCAN_USB_BREQ_BT_CONST_EXT   = 17,
	VCAN_USB_BREQ_BITTIMING      = 24,
	VCAN_USB_BREQ_DATA_BITTIMING = 25,
	VCAN_USB_BREQ_BSP_DEVICE_INFO = 33, /* version/UID/UUID */
	VCAN_USB_BREQ_USB_MODE       = 34,  /* switch USB mode, persist + reboot */
	VCAN_USB_BREQ_CAN_FILTERS    = 35,  /* RX filter table */
	VCAN_USB_BREQ_CAN_BUS_LOAD   = 36,  /* HAL_VCAN_USB_BREQ_CAN_BUS_LOAD */
	VCAN_USB_BREQ_CAN_TERMINATION = 37, /* HAL_VCAN_USB_BREQ_CAN_TERMINATION */
};

enum vcan_usb_can_mode {
	VCAN_USB_CHANNEL_MODE_RESET = 0,
	VCAN_USB_CHANNEL_MODE_START = 1,
};

enum vcan_usb_can_state {
	VCAN_USB_CAN_STATE_ERROR_ACTIVE  = 0,
	VCAN_USB_CAN_STATE_ERROR_WARNING = 1,
	VCAN_USB_CAN_STATE_ERROR_PASSIVE = 2,
	VCAN_USB_CAN_STATE_BUS_OFF       = 3,
	VCAN_USB_CAN_STATE_STOPPED       = 4,
	VCAN_USB_CAN_STATE_SLEEPING      = 5,
};

/* vcan_usb_device_berr.error_code: protocol-violation reason, valid only when
 * error_flag/error_code changed since the last BERR frame (mirrors the
 * firmware's hal_can_error_code_t).
 */
enum vcan_usb_error_code {
	VCAN_USB_ERROR_CODE_NONE   = 0,
	VCAN_USB_ERROR_CODE_STUFF  = 1,
	VCAN_USB_ERROR_CODE_FORM   = 2,
	VCAN_USB_ERROR_CODE_ACK    = 3,
	VCAN_USB_ERROR_CODE_BIT1   = 4,
	VCAN_USB_ERROR_CODE_BIT0   = 5,
	VCAN_USB_ERROR_CODE_CRC    = 6,
	VCAN_USB_ERROR_CODE_NO_CHANGE = 7,
	VCAN_USB_ERROR_CODE_UNKNOWN = 127,
};

/* mode_flags (and feature bits share the same layout) */
#define VCAN_USB_MODE_NORMAL        0U
#define VCAN_USB_MODE_LISTEN_ONLY   BIT(0)
#define VCAN_USB_MODE_LOOP_BACK     BIT(1)
#define VCAN_USB_MODE_TRIPLE_SAMPLE BIT(2)
#define VCAN_USB_MODE_ONE_SHOT      BIT(3)
#define VCAN_USB_MODE_HW_TIMESTAMP  BIT(4)
#define VCAN_USB_MODE_PAD_PKTS_TO_MAX BIT(7)
#define VCAN_USB_MODE_FD            BIT(8)
#define VCAN_USB_MODE_FD_NON_ISO    BIT(9)
#define VCAN_USB_MODE_BERR_REPORTING BIT(12)

#define VCAN_USB_FEATURE_LISTEN_ONLY   BIT(0)
#define VCAN_USB_FEATURE_LOOP_BACK     BIT(1)
#define VCAN_USB_FEATURE_TRIPLE_SAMPLE BIT(2)
#define VCAN_USB_FEATURE_ONE_SHOT      BIT(3)
#define VCAN_USB_FEATURE_HW_TIMESTAMP  BIT(4)
#define VCAN_USB_FEATURE_IDENTIFY      BIT(5)
#define VCAN_USB_FEATURE_FD            BIT(8)
#define VCAN_USB_FEATURE_BT_CONST_EXT  BIT(10)
#define VCAN_USB_FEATURE_TERMINATION   BIT(11)
#define VCAN_USB_FEATURE_BERR_REPORTING BIT(12)
#define VCAN_USB_FEATURE_GET_STATE     BIT(13)

/* per-frame flags (in the header flags field of data/RX frames) */
#define VCAN_USB_FLAG_OVERFLOW BIT(0)
#define VCAN_USB_FLAG_FD       BIT(1)
#define VCAN_USB_FLAG_BRS      BIT(2)
#define VCAN_USB_FLAG_ESI      BIT(3)
#define VCAN_USB_FLAG_EFF      BIT(4)
#define VCAN_USB_FLAG_RTR      BIT(5)
#define VCAN_USB_FLAG_ERR      BIT(6)

#define VCAN_USB_ID_MASK_STD 0x000007ffU
#define VCAN_USB_ID_MASK_EXT 0x1fffffffU

/* echo_id frame-direction markers */
#define VCAN_USB_ECHO_TX    0xA1C95E3DU
#define VCAN_USB_ECHO_RX    0xA2C95E3DU
#define VCAN_USB_ECHO_LOAD  0xA3C95E3DU
#define VCAN_USB_ECHO_STATE 0xA4C95E3DU
#define VCAN_USB_ECHO_SETUP 0xA5C95E3DU

/* opcode = (channel << 12) | size_in_bytes (low 12 bits) */
#define VCAN_USB_OPCODE_SIZE_MASK    0x0fffU
#define VCAN_USB_OPCODE_CHANNEL_SHIFT 12U
#define VCAN_USB_OPCODE(ch, size) \
	((u16)((((u16)(ch) << VCAN_USB_OPCODE_CHANNEL_SHIFT) & 0xf000U) | \
	       ((u16)(size) & VCAN_USB_OPCODE_SIZE_MASK)))
#define VCAN_USB_OPCODE_SIZE(op)    ((u16)((op) & VCAN_USB_OPCODE_SIZE_MASK))
#define VCAN_USB_OPCODE_CHANNEL(op) ((u8)(((op) >> VCAN_USB_OPCODE_CHANNEL_SHIFT) & 0x0fU))

#define VCAN_USB_TERMINATION_OFF 0
#define VCAN_USB_TERMINATION_ON  120

/* Common header carried by every VCAN frame. */
struct vcan_usb_hdr {
	__le32 echo_id;
	__le16 opcode;
	__le16 flags;
} __packed;

#define VCAN_USB_HDR_LEN ((u16)sizeof(struct vcan_usb_hdr))

struct vcan_usb_host_config {
	struct vcan_usb_hdr hdr;
	__le32 byte_order;
} __packed;

struct vcan_usb_device_mode {
	struct vcan_usb_hdr hdr;
	__le32 mode;
	__le32 mode_flags;
} __packed;

struct vcan_usb_device_bittiming {
	struct vcan_usb_hdr hdr;
	__le32 prop_seg;
	__le32 phase_seg1;
	__le32 phase_seg2;
	__le32 sjw;
	__le32 brp;
} __packed;

struct vcan_usb_bt_const_fields {
	__le32 tseg1_min;
	__le32 tseg1_max;
	__le32 tseg2_min;
	__le32 tseg2_max;
	__le32 sjw_max;
	__le32 brp_min;
	__le32 brp_max;
	__le32 brp_inc;
} __packed;

struct vcan_usb_bt_const {
	struct vcan_usb_hdr hdr;
	__le32 feature;
	__le32 fclk_can;
	struct vcan_usb_bt_const_fields btc;
} __packed;

struct vcan_usb_bt_const_ext {
	struct vcan_usb_hdr hdr;
	__le32 feature;
	__le32 fclk_can;
	struct vcan_usb_bt_const_fields btc;
	struct vcan_usb_bt_const_fields dbtc;
} __packed;

struct vcan_usb_can_config {
	struct vcan_usb_hdr hdr;
	__le32 state;
} __packed;

struct vcan_usb_bsp_device_info {
	struct vcan_usb_hdr hdr;
	__le32 sw_version;
	__le32 hw_version;
	__le32 uid[4];
	__le32 uuid[4];
} __packed;

struct vcan_usb_device_state {
	struct vcan_usb_hdr hdr;
	__le64 timestamp_us;
	__le32 state;
	__le32 rxerr;
	__le32 txerr;
} __packed;

struct vcan_usb_device_berr {
	struct vcan_usb_hdr hdr;
	u8 error_flag;
	u8 error_code;
	u8 rx_error_count;
	u8 tx_error_count;
	u8 error_logging_count;
	u8 reserved[3];
} __packed;

/* Data / RX frame: header, then CAN id, dlc, timestamp, payload at offset 24. */
struct vcan_usb_host_frame {
	struct vcan_usb_hdr hdr;
	__le32 can_id;
	u8 dlc;
	u8 reserved[3];
	__le64 timestamp_us;
	u8 data[]; /* 8 (classic) or 64 (FD) */
} __packed;

#define VCAN_USB_FRAME_DATA_OFFSET ((u16)offsetof(struct vcan_usb_host_frame, data))

#endif /* _VCAN_USB_H */
