/* SPDX-License-Identifier: GPL-2.0 */
/*
 * VKGS USB (candleLight-compatible) protocol definitions for the Linux
 * SocketCAN driver.
 *
 * Mirrors the on-wire protocol of the HPMicro VEK gs_can firmware
 * (ref/firmware/vcan_0_0_2). The firmware is a standard gs_usb/candleLight device
 * that exposes one USB interface per CAN channel and adds a small set of
 * private vendor requests
 * (USB_MODE/CAN_FILTERS/CAN_BUS_LOAD/CAN_TERMINATION/BSP_DEVICE_INFO).
 *
 * Keep this header self-contained: it does not include the firmware SDK so the
 * module can be built out-of-tree by DKMS.
 */

#ifndef _VKGS_USB_H
#define _VKGS_USB_H

#include <linux/types.h>

#define VKGS_USB_DRIVER_NAME  "vkgs_usb"

/* USB identity. The firmware deliberately reuses the gs_usb/candleLight PID, so
 * the in-tree gs_usb driver must be unbound/blacklisted for this driver to bind.
 */
#define VKGS_USB_VID          0x1d50
#define VKGS_USB_PID          0x606f

/* CAN controller clock advertised by the firmware (also read back at probe). */
#define VKGS_USB_CLOCK_HZ     80000000U

/* Bulk IN/OUT endpoints are discovered per interface from the USB descriptor
 * (usb_find_common_endpoints); the driver never hard-codes endpoint addresses,
 * so any number of independent channels/interfaces is supported.
 */

/* gs_usb vendor requests (bRequest). 0..14 are standard gs_usb; 33..37 are the
 * VKGS firmware extensions called out in the protocol spec.
 */
enum vkgs_usb_breq {
	VKGS_USB_BREQ_HOST_FORMAT    = 0,
	VKGS_USB_BREQ_BITTIMING      = 1,
	VKGS_USB_BREQ_MODE           = 2,
	VKGS_USB_BREQ_BERR           = 3,
	VKGS_USB_BREQ_BT_CONST       = 4,
	VKGS_USB_BREQ_DEVICE_CONFIG  = 5,
	VKGS_USB_BREQ_TIMESTAMP      = 6,
	VKGS_USB_BREQ_IDENTIFY       = 7,
	VKGS_USB_BREQ_GET_USER_ID    = 8,
	VKGS_USB_BREQ_SET_USER_ID    = 9,
	VKGS_USB_BREQ_DATA_BITTIMING = 10,
	VKGS_USB_BREQ_BT_CONST_EXT   = 11,
	VKGS_USB_BREQ_SET_TERMINATION = 12,
	VKGS_USB_BREQ_GET_TERMINATION = 13,
	VKGS_USB_BREQ_GET_STATE      = 14,

	VKGS_USB_BREQ_BSP_DEVICE_INFO = 33, /* version/UID/UUID */
	VKGS_USB_BREQ_USB_MODE            = 34, /* switch USB mode, persist + reboot */
	VKGS_USB_BREQ_CAN_FILTERS         = 35, /* RX filter table (dir by bmRequestType) */
	VKGS_USB_BREQ_CAN_BUS_LOAD        = 36, /* GS_USB_BREQ_CAN_BUS_LOAD */
	VKGS_USB_BREQ_CAN_TERMINATION     = 37, /* GS_USB_BREQ_CAN_TERMINATION */
};

enum vkgs_usb_can_mode {
	VKGS_USB_CAN_MODE_RESET = 0,
	VKGS_USB_CAN_MODE_START = 1,
};

enum vkgs_usb_can_state {
	VKGS_USB_CAN_STATE_ERROR_ACTIVE  = 0,
	VKGS_USB_CAN_STATE_ERROR_WARNING = 1,
	VKGS_USB_CAN_STATE_ERROR_PASSIVE = 2,
	VKGS_USB_CAN_STATE_BUS_OFF       = 3,
	VKGS_USB_CAN_STATE_STOPPED       = 4,
	VKGS_USB_CAN_STATE_SLEEPING      = 5,
};

/* vkgs_usb_berr_ext.error_code: protocol-violation reason, shared with the
 * vcan_usb protocol (same firmware-internal hal_can_error_code_t).
 */
enum vkgs_usb_error_code {
	VKGS_USB_ERROR_CODE_NONE   = 0,
	VKGS_USB_ERROR_CODE_STUFF  = 1,
	VKGS_USB_ERROR_CODE_FORM   = 2,
	VKGS_USB_ERROR_CODE_ACK    = 3,
	VKGS_USB_ERROR_CODE_BIT1   = 4,
	VKGS_USB_ERROR_CODE_BIT0   = 5,
	VKGS_USB_ERROR_CODE_CRC    = 6,
	VKGS_USB_ERROR_CODE_NO_CHANGE = 7,
	VKGS_USB_ERROR_CODE_UNKNOWN = 127,
};

/* gs_device_mode.flags / feature bits (shared bit layout with mainline gs_usb) */
#define VKGS_USB_MODE_NORMAL          0
#define VKGS_USB_MODE_LISTEN_ONLY     BIT(0)
#define VKGS_USB_MODE_LOOP_BACK       BIT(1)
#define VKGS_USB_MODE_TRIPLE_SAMPLE   BIT(2)
#define VKGS_USB_MODE_ONE_SHOT        BIT(3)
#define VKGS_USB_MODE_HW_TIMESTAMP    BIT(4)
#define VKGS_USB_MODE_PAD_PKTS_TO_MAX BIT(7)
#define VKGS_USB_MODE_FD              BIT(8)
#define VKGS_USB_MODE_FD_NON_ISO      BIT(9)
#define VKGS_USB_MODE_BERR_REPORTING  BIT(12)

#define VKGS_USB_FEATURE_LISTEN_ONLY     BIT(0)
#define VKGS_USB_FEATURE_LOOP_BACK       BIT(1)
#define VKGS_USB_FEATURE_TRIPLE_SAMPLE   BIT(2)
#define VKGS_USB_FEATURE_ONE_SHOT        BIT(3)
#define VKGS_USB_FEATURE_HW_TIMESTAMP    BIT(4)
#define VKGS_USB_FEATURE_IDENTIFY        BIT(5)
#define VKGS_USB_FEATURE_USER_ID         BIT(6)
#define VKGS_USB_FEATURE_PAD_PKTS_TO_MAX BIT(7)
#define VKGS_USB_FEATURE_FD              BIT(8)
#define VKGS_USB_FEATURE_BT_CONST_EXT    BIT(10)
#define VKGS_USB_FEATURE_TERMINATION     BIT(11)
#define VKGS_USB_FEATURE_BERR_REPORTING  BIT(12)
#define VKGS_USB_FEATURE_GET_STATE       BIT(13)

/* host_frame flags */
#define VKGS_USB_FLAG_OVERFLOW BIT(0)
#define VKGS_USB_FLAG_FD       BIT(1)
#define VKGS_USB_FLAG_BRS      BIT(2)
#define VKGS_USB_FLAG_ESI      BIT(3)

/* host_frame can_id flags (mirror linux/can.h) */
#define VKGS_USB_CAN_FLAG_EFF 0x80000000U
#define VKGS_USB_CAN_FLAG_RTR 0x40000000U
#define VKGS_USB_CAN_FLAG_ERR 0x20000000U

/* echo_id markers used on the IN endpoint */
#define VKGS_USB_ECHO_TX    0xFFFFFFFEU
#define VKGS_USB_ECHO_RX    0xFFFFFFFFU
#define VKGS_USB_ECHO_LOAD  0xA3C95E3DU
#define VKGS_USB_ECHO_STATE 0xA4C95E3DU
#define VKGS_USB_ECHO_BERR  0xA6C95E3DU

#define VKGS_USB_TERMINATION_OFF 0
#define VKGS_USB_TERMINATION_ON  120

struct vkgs_usb_host_config {
	__le32 byte_order;
} __packed;

struct vkgs_usb_device_config {
	u8 reserved1;
	u8 reserved2;
	u8 reserved3;
	u8 icount;
	__le32 sw_version;
	__le32 hw_version;
} __packed;

struct vkgs_usb_device_mode {
	__le32 mode;
	__le32 flags;
} __packed;

struct vkgs_usb_identify_mode {
	__le32 mode;
} __packed;

struct vkgs_usb_device_bittiming {
	__le32 prop_seg;
	__le32 phase_seg1;
	__le32 phase_seg2;
	__le32 sjw;
	__le32 brp;
} __packed;

struct vkgs_usb_bt_const_fields {
	__le32 tseg1_min;
	__le32 tseg1_max;
	__le32 tseg2_min;
	__le32 tseg2_max;
	__le32 sjw_max;
	__le32 brp_min;
	__le32 brp_max;
	__le32 brp_inc;
} __packed;

struct vkgs_usb_bt_const {
	__le32 feature;
	__le32 fclk_can;
	struct vkgs_usb_bt_const_fields btc;
} __packed;

struct vkgs_usb_bt_const_ext {
	__le32 feature;
	__le32 fclk_can;
	struct vkgs_usb_bt_const_fields btc;
	struct vkgs_usb_bt_const_fields dbtc;
} __packed;

/* VKGS_USB_BREQ_BSP_DEVICE_INFO (33) */
struct vkgs_usb_bsp_device_info {
	__le32 sw_version;
	__le32 hw_version;
	__le32 uid[4];
	__le32 uuid[4];
} __packed;

/* GS_USB_BREQ_CAN_BUS_LOAD (36) / CAN_TERMINATION (37) */
struct vkgs_usb_can_config {
	__le32 state;
} __packed;

/* GS_USB_BREQ_CAN_FILTERS (35) one filter row */
struct vkgs_usb_id_filter {
	u8 filter_enable;
	u8 filter_type;
	u8 filter_config;
	u8 can_id_type;
	__le32 filter_id;
	__le32 filter_mask;
} __packed;

struct vkgs_usb_can_filter {
	u8 target;
	u8 filter_index;
	u8 reserved[2];
	struct vkgs_usb_id_filter id_filter;
} __packed;

/* Extended async event frames delivered on the bulk IN endpoint. */
struct vkgs_usb_state_ext {
	__le32 echo_id;
	u8 channel;
	u8 reserved;
	__le16 flags;
	__le64 timestamp_us;
	__le32 state;
	__le32 rxerr;
	__le32 txerr;
} __packed;

struct vkgs_usb_berr_ext {
	__le32 echo_id;
	u8 channel;
	u8 reserved;
	__le16 flags;
	u8 error_flag;
	u8 error_code;
	u8 rx_error_count;
	u8 tx_error_count;
	u8 error_logging_count;
	u8 reserved2[3];
} __packed;

struct vkgs_usb_load {
	__le32 echo_id;
	u8 channel;
	u8 reserved;
	__le16 flags;
	__le64 timestamp_us;
	__le16 bus_load;
	__le16 reserved2;
	__le32 tx_time_ns;
	__le32 rx_time_ns;
} __packed;

/* CAN data / TX-echo frame header. echo_id is kept native (LE host) to match
 * mainline gs_usb. A variable data area (8 or 64 bytes) follows.
 */
struct vkgs_usb_host_frame {
	u32 echo_id;
	__le32 can_id;
	u8 can_dlc;
	u8 channel;
	u8 flags;
	u8 reserved;
	u8 data[]; /* 8 (classic) or 64 (FD) */
} __packed;

#define VKGS_USB_HF_HDR_LEN  ((u16)sizeof(struct vkgs_usb_host_frame))

#endif /* _VKGS_USB_H */
