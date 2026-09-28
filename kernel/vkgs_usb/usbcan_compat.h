/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _USBCAN_COMPAT_H
#define _USBCAN_COMPAT_H

#include <linux/can/dev.h>
#include <linux/can/error.h>

/* Error-counter validity flag is absent from older supported headers. */
#ifndef CAN_ERR_CNT
#define CAN_ERR_CNT 0x00000200U
#endif
#include <linux/version.h>

/* Keep the supported range explicit: every API below is present in v4.12. */
#if LINUX_VERSION_CODE < KERNEL_VERSION(4, 12, 0)
#error "vkgs_usb requires Linux 4.12 or newer"
#endif

#define USBCAN_ERROR_WARNING_THRESHOLD 96U
#define USBCAN_ERROR_PASSIVE_THRESHOLD 128U

/* REF BSP_INFO: hardware byte 2 is the OTA platform identifier, not USB speed. */
#define USBCAN_HW_PLATFORM_SHIFT 16U
#define USBCAN_HW_PLATFORM_MASK 0xffU
#define USBCAN_HW_PLATFORM_HC32 32U
#define USBCAN_HC32_NOMINAL_TSEG1_MAX 64U
#define USBCAN_HC32_DATA_TSEG1_MAX 16U
#define USBCAN_HC32_TSEG2_MAX 8U
#define USBCAN_HC32_SJW_MAX 8U
#define USBCAN_HC32_BRP_MAX 256U

static inline bool usbcan_is_hc32(u32 hw_version)
{
	return ((hw_version >> USBCAN_HW_PLATFORM_SHIFT) &
		USBCAN_HW_PLATFORM_MASK) == USBCAN_HW_PLATFORM_HC32;
}

/* Older HC32 firmware advertises generic limits wider than its BSP accepts.
 * Nominal limits must work in Classic as well as FD. HC32 TimeSeg1 includes
 * SyncSeg, so BSP maxima 65/17 correspond to SocketCAN tseg1 maxima 64/16.
 */
static inline void usbcan_hc32_timing_limits(struct can_bittiming_const *btc,
					   bool data_phase)
{
	btc->tseg1_max = min(btc->tseg1_max, data_phase ?
		USBCAN_HC32_DATA_TSEG1_MAX : USBCAN_HC32_NOMINAL_TSEG1_MAX);
	btc->tseg2_max = min(btc->tseg2_max, USBCAN_HC32_TSEG2_MAX);
	btc->sjw_max = min(btc->sjw_max, USBCAN_HC32_SJW_MAX);
	btc->brp_max = min(btc->brp_max, USBCAN_HC32_BRP_MAX);
}

/* USB bulk endpoint limits; reject malformed descriptors before ALIGN(). */
static inline bool usbcan_valid_bulk_mps(unsigned int size)
{
	switch (size) {
	case 8: case 16: case 32: case 64: case 512: case 1024:
		return true;
	default:
		return false;
	}
}

static inline int usbcan_put_echo_skb(struct sk_buff *skb,
				      struct net_device *ndev,
				      unsigned int idx, unsigned int len)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 12, 0)
	return can_put_echo_skb(skb, ndev, idx, len);
#elif LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)
	(void)len;
	return can_put_echo_skb(skb, ndev, idx);
#else
	(void)len;
	can_put_echo_skb(skb, ndev, idx);
	return 0;
#endif
}

static inline unsigned int usbcan_get_echo_skb(struct net_device *ndev,
					       unsigned int idx)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 12, 0)
	return can_get_echo_skb(ndev, idx, NULL);
#else
	return can_get_echo_skb(ndev, idx);
#endif
}

static inline void usbcan_free_echo_skb(struct net_device *ndev,
					unsigned int idx)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 13, 0)
	can_free_echo_skb(ndev, idx, NULL);
#else
	can_free_echo_skb(ndev, idx);
#endif
}

static inline u8 usbcan_cc_dlc2len(u8 dlc)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
	return can_cc_dlc2len(dlc);
#else
	return can_dlc2len(dlc);
#endif
}

static inline u8 usbcan_fd_dlc2len(u8 dlc)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
	return can_fd_dlc2len(dlc);
#else
	return can_dlc2len(dlc);
#endif
}

static inline u8 usbcan_fd_len2dlc(u8 len)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
	return can_fd_len2dlc(len);
#else
	return can_len2dlc(len);
#endif
}

static inline u8 usbcan_get_cc_dlc(const struct can_frame *cf, u32 ctrlmode)
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 11, 0)
	return can_get_cc_dlc(cf, ctrlmode);
#else
	(void)ctrlmode;
	return cf->can_dlc;
#endif
}

#endif /* _USBCAN_COMPAT_H */
