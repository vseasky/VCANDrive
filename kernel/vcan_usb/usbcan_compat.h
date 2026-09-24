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
#error "vcan_usb requires Linux 4.12 or newer"
#endif

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
