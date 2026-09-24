// SPDX-License-Identifier: GPL-2.0
/*
 * VKGS USB USB SocketCAN driver.
 *
 * Out-of-tree driver for the GS_CAN device firmware.
 * The device is a candleLight/gs_usb-compatible USB-CAN(FD) adapter that
 * exposes ONE USB interface per CAN channel (each with its own bulk endpoint
 * pair) and adds private vendor requests for termination (CAN_TERMINATION) and
 * device info (VKGS_USB_BREQ_BSP_DEVICE_INFO).
 *
 * Design notes that differ from mainline gs_usb (all verified against the
 * firmware):
 *   - One netdev per USB interface; the driver probes per interface and does
 *     not limit the channel count (it follows the device's interface count).
 *   - The firmware does NOT echo transmitted frames, so TX completion (echo
 *     skb + tx stats) happens on the bulk-OUT URB completion.
 *   - Bit timing is register-encoded: the firmware adds 1 to every segment and
 *     ignores prop_seg, so the host sends brp-1, (prop_seg+phase_seg1)-1,
 *     phase_seg2-1, sjw-1.
 *   - Termination and bus-load reporting prefer the firmware's private requests.
 *     Standard gs_usb termination requests remain as a compatibility fallback.
 *   - HW timestamps are intentionally not requested, keeping RX simple and the
 *     receive path portable. Compatibility helpers support upstream kernels
 *     from 4.12 onward.
 */

#include <linux/can.h>
#include <linux/can/dev.h>
#include <linux/can/error.h>
#include <linux/delay.h>
#include <linux/ethtool.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/slab.h>
#include <linux/usb.h>

#include "vkgs_usb.h"
#include "usbcan_compat.h"

#define VKGS_USB_DRV_VERSION   "1.1.4"
#define VKGS_USB_MAX_TX_URBS   10
#define VKGS_USB_MAX_RX_URBS   30
#define VKGS_USB_CTRL_TIMEOUT  1000
#define VKGS_USB_MODE_SETTLE_MS 150

struct vkgs_usb;

struct vkgs_usb_tx_ctx {
	struct vkgs_usb *dev;
	u32 echo_id;
	u8 data_len;
};

struct vkgs_usb {
	struct can_priv can; /* must be first */
	struct net_device *netdev;
	struct usb_device *udev;
	struct usb_interface *intf;

	u8 channel;
	u32 feature;
	unsigned int pipe_in;
	unsigned int pipe_out;
	unsigned int hf_size_tx;
	unsigned int rx_buf_sz;

	struct can_bittiming_const bt_const;
	struct can_bittiming_const data_bt_const;

	struct usb_anchor tx_submitted;
	struct usb_anchor rx_submitted;
	atomic_t active_tx_urbs;
	spinlock_t tx_ctx_lock;
	spinlock_t stats_lock;
	struct vkgs_usb_tx_ctx tx_context[VKGS_USB_MAX_TX_URBS];

	struct can_berr_counter bec;
	bool termination;
	bool rx_running;
};

static void vkgs_usb_account_rx(struct vkgs_usb *dev, unsigned int len,
				 bool overflow)
{
	unsigned long flags;

	spin_lock_irqsave(&dev->stats_lock, flags);
	dev->netdev->stats.rx_packets++;
	dev->netdev->stats.rx_bytes += len;
	if (overflow)
		dev->netdev->stats.rx_over_errors++;
	spin_unlock_irqrestore(&dev->stats_lock, flags);
}

static void vkgs_usb_account_tx(struct vkgs_usb *dev, unsigned int len)
{
	unsigned long flags;

	spin_lock_irqsave(&dev->stats_lock, flags);
	dev->netdev->stats.tx_packets++;
	dev->netdev->stats.tx_bytes += len;
	spin_unlock_irqrestore(&dev->stats_lock, flags);
}

/* ---- TX context bookkeeping ------------------------------------------- */

static struct vkgs_usb_tx_ctx *vkgs_usb_alloc_tx_ctx(struct vkgs_usb *dev)
{
	unsigned long flags;
	int i;

	spin_lock_irqsave(&dev->tx_ctx_lock, flags);
	for (i = 0; i < VKGS_USB_MAX_TX_URBS; i++) {
		if (dev->tx_context[i].echo_id == VKGS_USB_MAX_TX_URBS) {
			dev->tx_context[i].echo_id = i;
			spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
			return &dev->tx_context[i];
		}
	}
	spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
	return NULL;
}

static void vkgs_usb_free_tx_ctx(struct vkgs_usb_tx_ctx *ctx)
{
	struct vkgs_usb *dev = ctx->dev;
	unsigned long flags;

	spin_lock_irqsave(&dev->tx_ctx_lock, flags);
	ctx->echo_id = VKGS_USB_MAX_TX_URBS;
	spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
}

static void vkgs_usb_init_tx_ctx(struct vkgs_usb *dev)
{
	int i;

	for (i = 0; i < VKGS_USB_MAX_TX_URBS; i++) {
		dev->tx_context[i].dev = dev;
		dev->tx_context[i].echo_id = VKGS_USB_MAX_TX_URBS;
	}
}

/* ---- control transfers ------------------------------------------------- */

static int vkgs_usb_send(struct vkgs_usb *dev, u8 request, void *buf,
			  u16 len)
{
	void *dma = kmemdup(buf, len, GFP_KERNEL);
	int ret;

	if (!dma)
		return -ENOMEM;

	ret = usb_control_msg(dev->udev, usb_sndctrlpipe(dev->udev, 0),
			      request,
			      USB_DIR_OUT | USB_TYPE_VENDOR | USB_RECIP_INTERFACE,
			      dev->channel, dev->channel, dma, len,
			      VKGS_USB_CTRL_TIMEOUT);
	kfree(dma);
	return ret < 0 ? ret : (ret == len ? 0 : -EIO);
}

static int vkgs_usb_recv(struct vkgs_usb *dev, u8 request, u16 value,
			  void *buf, u16 len)
{
	void *dma = kzalloc(len, GFP_KERNEL);
	int ret;

	if (!dma)
		return -ENOMEM;

	ret = usb_control_msg(dev->udev, usb_rcvctrlpipe(dev->udev, 0),
			      request,
			      USB_DIR_IN | USB_TYPE_VENDOR | USB_RECIP_INTERFACE,
			      value, dev->channel, dma, len,
			      VKGS_USB_CTRL_TIMEOUT);
	if (ret >= 0)
		memcpy(buf, dma, len);
	kfree(dma);
	return ret < 0 ? ret : (ret == len ? 0 : -EIO);
}

static int vkgs_usb_set_mode_cmd(struct vkgs_usb *dev, u32 mode, u32 flags)
{
	struct vkgs_usb_device_mode dm = {
		.mode = cpu_to_le32(mode),
		.flags = cpu_to_le32(flags),
	};

	return vkgs_usb_send(dev, VKGS_USB_BREQ_MODE, &dm, sizeof(dm));
}

static int vkgs_usb_host_format(struct vkgs_usb *dev)
{
	struct vkgs_usb_host_config hconf = {
		.byte_order = cpu_to_le32(0x0000beef),
	};

	return vkgs_usb_send(dev, VKGS_USB_BREQ_HOST_FORMAT, &hconf,
			     sizeof(hconf));
}

static int vkgs_usb_get_device_info(struct vkgs_usb *dev,
				    struct vkgs_usb_bsp_device_info *info)
{
	return vkgs_usb_recv(dev, VKGS_USB_BREQ_BSP_DEVICE_INFO, 0, info,
			     sizeof(*info));
}

static u32 vkgs_usb_start_flags(struct vkgs_usb *dev)
{
	u32 ctrlmode = dev->can.ctrlmode;
	u32 flags = 0;

	if (ctrlmode & CAN_CTRLMODE_LOOPBACK)
		flags |= VKGS_USB_MODE_LOOP_BACK;
	if (ctrlmode & CAN_CTRLMODE_LISTENONLY)
		flags |= VKGS_USB_MODE_LISTEN_ONLY;
	if (ctrlmode & CAN_CTRLMODE_3_SAMPLES)
		flags |= VKGS_USB_MODE_TRIPLE_SAMPLE;
	if (ctrlmode & CAN_CTRLMODE_ONE_SHOT)
		flags |= VKGS_USB_MODE_ONE_SHOT;
	if (ctrlmode & CAN_CTRLMODE_FD)
		flags |= VKGS_USB_MODE_FD;
#ifdef CAN_CTRLMODE_FD_NON_ISO
	if (ctrlmode & CAN_CTRLMODE_FD_NON_ISO)
		flags |= VKGS_USB_MODE_FD_NON_ISO;
#endif
	if (ctrlmode & CAN_CTRLMODE_BERR_REPORTING)
		flags |= VKGS_USB_MODE_BERR_REPORTING;
	return flags;
}

/* Firmware adds 1 to every segment and ignores prop_seg (register-encoded
 * timing), so fold prop_seg into phase_seg1 and subtract 1 from each field.
 */
static void vkgs_usb_fill_bittiming(struct vkgs_usb_device_bittiming *dbt,
				     const struct can_bittiming *bt)
{
	dbt->prop_seg = cpu_to_le32(0);
	dbt->phase_seg1 = cpu_to_le32(bt->prop_seg + bt->phase_seg1 - 1);
	dbt->phase_seg2 = cpu_to_le32(bt->phase_seg2 - 1);
	dbt->sjw = cpu_to_le32(bt->sjw - 1);
	dbt->brp = cpu_to_le32(bt->brp - 1);
}

static int vkgs_usb_set_bittiming(struct net_device *netdev)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	struct vkgs_usb_device_bittiming dbt = { 0 };

	vkgs_usb_fill_bittiming(&dbt, &dev->can.bittiming);
	return vkgs_usb_send(dev, VKGS_USB_BREQ_BITTIMING, &dbt, sizeof(dbt));
}

static int vkgs_usb_set_data_bittiming(struct net_device *netdev)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	struct vkgs_usb_device_bittiming dbt = { 0 };

	vkgs_usb_fill_bittiming(&dbt, &dev->can.data_bittiming);
	return vkgs_usb_send(dev, VKGS_USB_BREQ_DATA_BITTIMING, &dbt,
			      sizeof(dbt));
}

static int vkgs_usb_set_termination_cmd(struct vkgs_usb *dev, bool on)
{
	struct vkgs_usb_can_config cfg = {
		.state = cpu_to_le32(on ? 1 : 0),
	};
	int ret;

	ret = vkgs_usb_send(dev, VKGS_USB_BREQ_CAN_TERMINATION, &cfg,
			    sizeof(cfg));
	if (ret)
		ret = vkgs_usb_send(dev, VKGS_USB_BREQ_SET_TERMINATION, &cfg,
				    sizeof(cfg));
	return ret;
}

static int vkgs_usb_set_bus_load_cmd(struct vkgs_usb *dev, bool on)
{
	struct vkgs_usb_can_config cfg = {
		.state = cpu_to_le32(on ? 1 : 0),
	};

	return vkgs_usb_send(dev, VKGS_USB_BREQ_CAN_BUS_LOAD, &cfg,
			     sizeof(cfg));
}

static int vkgs_usb_get_termination(struct vkgs_usb *dev, bool *on)
{
	struct vkgs_usb_can_config cfg;
	int ret;

	ret = vkgs_usb_recv(dev, VKGS_USB_BREQ_CAN_TERMINATION, 0, &cfg,
			     sizeof(cfg));
	if (ret)
		ret = vkgs_usb_recv(dev, VKGS_USB_BREQ_GET_TERMINATION, 0, &cfg,
				     sizeof(cfg));
	if (ret)
		return ret;
	*on = le32_to_cpu(cfg.state) != 0;
	return 0;
}

/* ---- RX / event handling ---------------------------------------------- */

static void vkgs_usb_set_state(struct vkgs_usb *dev, u32 dev_state)
{
	struct net_device *ndev = dev->netdev;
	enum can_state old = dev->can.state;
	enum can_state new;

	switch (dev_state) {
	case VKGS_USB_CAN_STATE_BUS_OFF:
		new = CAN_STATE_BUS_OFF;
		break;
	case VKGS_USB_CAN_STATE_ERROR_PASSIVE:
		new = CAN_STATE_ERROR_PASSIVE;
		break;
	case VKGS_USB_CAN_STATE_ERROR_WARNING:
		new = CAN_STATE_ERROR_WARNING;
		break;
	case VKGS_USB_CAN_STATE_ERROR_ACTIVE:
		new = CAN_STATE_ERROR_ACTIVE;
		break;
	default:
		new = CAN_STATE_STOPPED;
		break;
	}

	if (new == old)
		return;
	dev->can.state = new;

	if (new == CAN_STATE_BUS_OFF) {
		dev->can.can_stats.bus_off++;
		can_bus_off(ndev);
	} else if (new == CAN_STATE_ERROR_WARNING) {
		dev->can.can_stats.error_warning++;
	} else if (new == CAN_STATE_ERROR_PASSIVE) {
		dev->can.can_stats.error_passive++;
	}
}

static void vkgs_usb_handle_state(struct vkgs_usb *dev,
				   const struct vkgs_usb_state_ext *st)
{
	struct net_device *ndev = dev->netdev;
	u32 state = le32_to_cpu(st->state);
	struct can_frame *cf;
	struct sk_buff *skb;

	dev->bec.rxerr = le32_to_cpu(st->rxerr);
	dev->bec.txerr = le32_to_cpu(st->txerr);
	vkgs_usb_set_state(dev, state);

	/* ERROR_ACTIVE is normal operation.  Do not turn recovery or duplicate
	 * ACTIVE/STOPPED notifications into empty SocketCAN error frames.
	 */
	if (state != VKGS_USB_CAN_STATE_BUS_OFF &&
	    state != VKGS_USB_CAN_STATE_ERROR_PASSIVE &&
	    state != VKGS_USB_CAN_STATE_ERROR_WARNING)
		return;

	skb = alloc_can_err_skb(ndev, &cf);
	if (!skb)
		return;

	if (state == VKGS_USB_CAN_STATE_BUS_OFF) {
		cf->can_id |= CAN_ERR_BUSOFF;
	} else if (state == VKGS_USB_CAN_STATE_ERROR_PASSIVE) {
		cf->can_id |= CAN_ERR_CRTL;
		if (dev->bec.rxerr >= 128)
			cf->data[1] |= CAN_ERR_CRTL_RX_PASSIVE;
		if (dev->bec.txerr >= 128)
			cf->data[1] |= CAN_ERR_CRTL_TX_PASSIVE;
	} else if (state == VKGS_USB_CAN_STATE_ERROR_WARNING) {
		cf->can_id |= CAN_ERR_CRTL;
		if (dev->bec.rxerr >= 96)
			cf->data[1] |= CAN_ERR_CRTL_RX_WARNING;
		if (dev->bec.txerr >= 96)
			cf->data[1] |= CAN_ERR_CRTL_TX_WARNING;
	}
	cf->can_id |= CAN_ERR_CNT;
	cf->data[6] = dev->bec.txerr;
	cf->data[7] = dev->bec.rxerr;

	ndev->stats.rx_packets++;
	ndev->stats.rx_bytes += cf->len;
	netif_rx(skb);
}

/* error_code -> CAN_ERR_PROT_* mapping mirrors the firmware's
 * hal_can_error_code_t (see hal_can_reg.h, shared with the vcan_usb
 * protocol). Only emitted when the user has opted in via
 * CAN_CTRLMODE_BERR_REPORTING; rx/tx error counters are always cached
 * regardless, since do_get_berr_counter() relies on them.
 */
static void vkgs_usb_handle_berr(struct vkgs_usb *dev,
				  const struct vkgs_usb_berr_ext *be)
{
	struct net_device *ndev = dev->netdev;
	struct can_frame *cf;
	struct sk_buff *skb;

	dev->bec.rxerr = be->rx_error_count;
	dev->bec.txerr = be->tx_error_count;

	if (!(dev->can.ctrlmode & CAN_CTRLMODE_BERR_REPORTING))
		return;
	if (be->error_code == VKGS_USB_ERROR_CODE_NONE ||
	    be->error_code == VKGS_USB_ERROR_CODE_NO_CHANGE)
		return;

	skb = alloc_can_err_skb(ndev, &cf);
	if (!skb)
		return;

	cf->can_id |= CAN_ERR_PROT;
	switch (be->error_code) {
	case VKGS_USB_ERROR_CODE_STUFF:
		cf->data[2] |= CAN_ERR_PROT_STUFF;
		break;
	case VKGS_USB_ERROR_CODE_FORM:
		cf->data[2] |= CAN_ERR_PROT_FORM;
		break;
	case VKGS_USB_ERROR_CODE_ACK:
		cf->can_id |= CAN_ERR_ACK;
		cf->data[3] = CAN_ERR_PROT_LOC_ACK;
		break;
	case VKGS_USB_ERROR_CODE_BIT1:
		cf->data[2] |= CAN_ERR_PROT_BIT1;
		break;
	case VKGS_USB_ERROR_CODE_BIT0:
		cf->data[2] |= CAN_ERR_PROT_BIT0;
		break;
	case VKGS_USB_ERROR_CODE_CRC:
		cf->data[3] |= CAN_ERR_PROT_LOC_CRC_SEQ;
		break;
	default:
		cf->data[2] |= CAN_ERR_PROT_UNSPEC;
		break;
	}
	cf->data[6] = dev->bec.txerr;
	cf->data[7] = dev->bec.rxerr;

	ndev->stats.rx_packets++;
	ndev->stats.rx_bytes += cf->len;
	netif_rx(skb);
}

static void vkgs_usb_handle_rx_frame(struct vkgs_usb *dev,
				      const struct vkgs_usb_host_frame *hf)
{
	struct net_device *ndev = dev->netdev;
	u32 raw_id = le32_to_cpu(hf->can_id);
	struct canfd_frame *cfd;
	struct can_frame *cf;
	struct sk_buff *skb;
	unsigned int len;

	if (hf->flags & VKGS_USB_FLAG_FD) {
		if (raw_id & VKGS_USB_CAN_FLAG_RTR) {
			ndev->stats.rx_frame_errors++;
			ndev->stats.rx_dropped++;
			return;
		}
		skb = alloc_canfd_skb(ndev, &cfd);
		if (!skb) {
			ndev->stats.rx_dropped++;
			return;
		}
		cfd->len = usbcan_fd_dlc2len(hf->can_dlc & 0x0f);
		if (raw_id & VKGS_USB_CAN_FLAG_EFF) {
			cfd->can_id = raw_id & CAN_EFF_MASK;
			cfd->can_id |= CAN_EFF_FLAG;
		} else {
			cfd->can_id = raw_id & CAN_SFF_MASK;
		}
		/* This firmware duplicates FD ESI into the wire CAN-ID ERR bit
		 * (hal_gs_can_frame.c). ESI describes the sender's error state;
		 * it does not turn this data frame into a SocketCAN error frame.
		 * Preserve ESI below, but never propagate ERR into an FD can_id.
		 * Controller errors are delivered separately via STATE/BERR.
		 */
		if (hf->flags & VKGS_USB_FLAG_BRS)
			cfd->flags |= CANFD_BRS;
		if (hf->flags & VKGS_USB_FLAG_ESI)
			cfd->flags |= CANFD_ESI;
		memcpy(cfd->data, hf->data, cfd->len);
		len = cfd->len;
	} else {
		skb = alloc_can_skb(ndev, &cf);
		if (!skb) {
			ndev->stats.rx_dropped++;
			return;
		}
		if (raw_id & VKGS_USB_CAN_FLAG_EFF) {
			cf->can_id = raw_id & CAN_EFF_MASK;
			cf->can_id |= CAN_EFF_FLAG;
		} else {
			cf->can_id = raw_id & CAN_SFF_MASK;
		}
		if (raw_id & VKGS_USB_CAN_FLAG_RTR)
			cf->can_id |= CAN_RTR_FLAG;
		if (raw_id & VKGS_USB_CAN_FLAG_ERR)
			cf->can_id |= CAN_ERR_FLAG;
		cf->len = usbcan_cc_dlc2len(hf->can_dlc & 0x0f);
		if (!(cf->can_id & CAN_RTR_FLAG))
			memcpy(cf->data, hf->data, cf->len);
		len = (cf->can_id & CAN_RTR_FLAG) ? 0 : cf->len;
	}

	vkgs_usb_account_rx(dev, len, hf->flags & VKGS_USB_FLAG_OVERFLOW);
	netif_rx(skb);
}

/* Compute the on-wire size of one IN frame, given the bytes still available. */
static int vkgs_usb_frame_len(const u8 *buf, int avail)
{
	const struct vkgs_usb_host_frame *hf = (const void *)buf;
	u32 echo_id;

	if (avail < (int)VKGS_USB_HF_HDR_LEN)
		return -1;

	echo_id = hf->echo_id;
	switch (echo_id) {
	case VKGS_USB_ECHO_STATE:
		return sizeof(struct vkgs_usb_state_ext);
	case VKGS_USB_ECHO_BERR:
		return sizeof(struct vkgs_usb_berr_ext);
	case VKGS_USB_ECHO_LOAD:
		return sizeof(struct vkgs_usb_load);
	default:
		/* RX data frame (HW timestamps are never enabled by this driver,
		 * so the data area is the full 8 or 64 bytes).
		 */
		return VKGS_USB_HF_HDR_LEN +
		       ((hf->flags & VKGS_USB_FLAG_FD) ? 64 : 8);
	}
}

static void vkgs_usb_parse_bulk(struct vkgs_usb *dev, const u8 *buf, int len)
{
	struct net_device *ndev = dev->netdev;
	int off = 0;

	while (off < len) {
		const struct vkgs_usb_host_frame *hf =
			(const struct vkgs_usb_host_frame *)(buf + off);
		int flen;
		u8 frame_channel;

		/* The shared firmware FIFO terminates valid records with four
		 * zero bytes before padding the USB transfer.  Padding beyond the
		 * sentinel is not guaranteed to be cleared.
		 */
		if (len - off >= sizeof(hf->echo_id) && !hf->echo_id)
			break;

		flen = vkgs_usb_frame_len(buf + off, len - off);

		if (flen <= 0 || off + flen > len)
			break;

		if (hf->echo_id == VKGS_USB_ECHO_STATE ||
		    hf->echo_id == VKGS_USB_ECHO_BERR ||
		    hf->echo_id == VKGS_USB_ECHO_LOAD)
			frame_channel = ((const struct vkgs_usb_state_ext *)hf)->channel;
		else
			frame_channel = hf->channel;
		if (frame_channel != dev->channel) {
			ndev->stats.rx_frame_errors++;
			netdev_dbg(ndev, "rx frame channel %u != %u\n",
				   frame_channel, dev->channel);
			off += flen;
			continue;
		}

		switch (hf->echo_id) {
		case VKGS_USB_ECHO_RX:
			vkgs_usb_handle_rx_frame(dev, hf);
			break;
		case VKGS_USB_ECHO_STATE:
			vkgs_usb_handle_state(dev,
				(const struct vkgs_usb_state_ext *)hf);
			break;
		case VKGS_USB_ECHO_BERR:
			vkgs_usb_handle_berr(dev,
				(const struct vkgs_usb_berr_ext *)hf);
			break;
		case VKGS_USB_ECHO_LOAD:
			break; /* bus-load telemetry not exposed */
		default:
			/* The firmware never echoes TX frames; ignore stray ids. */
			netdev_dbg(ndev, "ignoring frame echo_id=0x%08x\n",
				   hf->echo_id);
			break;
		}
		off += flen;
	}
}

static void vkgs_usb_read_bulk_callback(struct urb *urb)
{
	struct vkgs_usb *dev = urb->context;
	struct net_device *ndev = dev->netdev;
	int ret;

	switch (urb->status) {
	case 0:
		if (READ_ONCE(dev->rx_running) && netif_running(ndev) &&
		    netif_device_present(ndev))
			vkgs_usb_parse_bulk(dev, urb->transfer_buffer,
					     urb->actual_length);
		break;
	case -ENOENT:
	case -ECONNRESET:
	case -ESHUTDOWN:
		return;
	default:
		/* transient error (e.g. -EOVERFLOW from packet-boundary
		 * padding): drop the frame but keep the pipeline alive.
		 */
		ndev->stats.rx_errors++;
		break;
	}

	if (!READ_ONCE(dev->rx_running) || !netif_device_present(ndev))
		return;

	usb_fill_bulk_urb(urb, dev->udev, dev->pipe_in, urb->transfer_buffer,
			  dev->rx_buf_sz, vkgs_usb_read_bulk_callback, dev);
	/* USB core unanchors before invoking the completion callback. */
	usb_anchor_urb(urb, &dev->rx_submitted);
	ret = usb_submit_urb(urb, GFP_ATOMIC);
	if (ret) {
		usb_unanchor_urb(urb);
		ndev->stats.rx_errors++;
		if (ret == -ENODEV)
			netif_device_detach(ndev);
		else
			netdev_warn(ndev, "rx urb resubmit failed: %d\n", ret);
	}
}

/* ---- TX --------------------------------------------------------------- */

static void vkgs_usb_write_bulk_callback(struct urb *urb)
{
	struct vkgs_usb_tx_ctx *ctx = urb->context;
	struct vkgs_usb *dev = ctx->dev;
	struct net_device *ndev = dev->netdev;
	unsigned int idx = ctx->echo_id;
	unsigned int echo_len;

	switch (urb->status) {
	case 0:
		/* The firmware sends no TX echo, so complete here. */
		echo_len = usbcan_get_echo_skb(ndev, idx);
		vkgs_usb_account_tx(dev, ctx->data_len);
		(void)echo_len;
		break;
	case -ENOENT:
	case -ECONNRESET:
	case -ESHUTDOWN:
		usbcan_free_echo_skb(ndev, idx);
		break;
	default:
		usbcan_free_echo_skb(ndev, idx);
		ndev->stats.tx_errors++;
		netdev_warn(ndev, "tx urb failed: %d\n", urb->status);
		break;
	}

	vkgs_usb_free_tx_ctx(ctx);
	/* Full barrier pairs with the stop/recheck in start_xmit(). */
	atomic_dec_return(&dev->active_tx_urbs);
	if (netif_running(ndev) && netif_device_present(ndev) &&
	    netif_carrier_ok(ndev) && netif_queue_stopped(ndev))
		netif_wake_queue(ndev);
}

static netdev_tx_t vkgs_usb_start_xmit(struct sk_buff *skb,
					struct net_device *netdev)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	struct net_device_stats *stats = &netdev->stats;
	struct vkgs_usb_host_frame *hf;
	struct vkgs_usb_tx_ctx *ctx;
	struct canfd_frame *cfd;
	struct can_frame *cf;
	unsigned int data_len;
	struct urb *urb;
	unsigned int idx;
	int ret;

	if (can_dropped_invalid_skb(netdev, skb))
		return NETDEV_TX_OK;

	ctx = vkgs_usb_alloc_tx_ctx(dev);
	if (!ctx) {
		netif_stop_queue(netdev);
		smp_mb();
		if (atomic_read(&dev->active_tx_urbs) < VKGS_USB_MAX_TX_URBS)
			netif_wake_queue(netdev);
		return NETDEV_TX_BUSY;
	}
	idx = ctx->echo_id;

	urb = usb_alloc_urb(0, GFP_ATOMIC);
	if (!urb)
		goto nomem_urb;

	hf = kzalloc(dev->hf_size_tx, GFP_ATOMIC);
	if (!hf)
		goto nomem_hf;

	hf->echo_id = idx;
	hf->channel = dev->channel;

	if (can_is_canfd_skb(skb)) {
		cfd = (struct canfd_frame *)skb->data;
		hf->can_id = cpu_to_le32(cfd->can_id);
		hf->can_dlc = usbcan_fd_len2dlc(cfd->len);
		hf->flags = VKGS_USB_FLAG_FD;
		if (cfd->flags & CANFD_BRS)
			hf->flags |= VKGS_USB_FLAG_BRS;
		if (cfd->flags & CANFD_ESI)
			hf->flags |= VKGS_USB_FLAG_ESI;
		data_len = cfd->len;
		memcpy(hf->data, cfd->data, data_len);
	} else {
		cf = (struct can_frame *)skb->data;
		hf->can_id = cpu_to_le32(cf->can_id);
		hf->can_dlc = usbcan_get_cc_dlc(cf, dev->can.ctrlmode);
		data_len = cf->len;
		if (!(cf->can_id & CAN_RTR_FLAG))
			memcpy(hf->data, cf->data, data_len);
	}
	ctx->data_len = (!can_is_canfd_skb(skb) &&
			 cf->can_id & CAN_RTR_FLAG) ? 0 : data_len;

	usb_fill_bulk_urb(urb, dev->udev, dev->pipe_out, hf, dev->hf_size_tx,
			  vkgs_usb_write_bulk_callback, ctx);
	urb->transfer_flags |= URB_FREE_BUFFER;
	usb_anchor_urb(urb, &dev->tx_submitted);

	ret = usbcan_put_echo_skb(skb, netdev, idx, data_len);
	if (unlikely(ret)) {
		usb_unanchor_urb(urb);
		usb_free_urb(urb);
		vkgs_usb_free_tx_ctx(ctx);
		stats->tx_dropped++;
		return NETDEV_TX_OK;
	}
	atomic_inc(&dev->active_tx_urbs);

	ret = usb_submit_urb(urb, GFP_ATOMIC);
	if (unlikely(ret)) {
		usbcan_free_echo_skb(netdev, idx);
		atomic_dec(&dev->active_tx_urbs);
		usb_unanchor_urb(urb);
		usb_free_urb(urb);
		vkgs_usb_free_tx_ctx(ctx);
		if (ret == -ENODEV) {
			netif_device_detach(netdev);
		} else {
			netdev_warn(netdev, "tx submit failed: %d\n", ret);
			stats->tx_dropped++;
		}
		return NETDEV_TX_OK;
	}

	usb_free_urb(urb);
	if (atomic_read(&dev->active_tx_urbs) >= VKGS_USB_MAX_TX_URBS) {
		netif_stop_queue(netdev);
		smp_mb();
		if (atomic_read(&dev->active_tx_urbs) < VKGS_USB_MAX_TX_URBS)
			netif_wake_queue(netdev);
	}
	return NETDEV_TX_OK;

nomem_hf:
	usb_free_urb(urb);
nomem_urb:
	vkgs_usb_free_tx_ctx(ctx);
	dev_kfree_skb(skb);
	stats->tx_dropped++;
	return NETDEV_TX_OK;
}

/* ---- netdev ops -------------------------------------------------------- */

static int vkgs_usb_alloc_rx_urbs(struct vkgs_usb *dev)
{
	struct net_device *ndev = dev->netdev;
	int i, ret;

	for (i = 0; i < VKGS_USB_MAX_RX_URBS; i++) {
		struct urb *urb;
		u8 *buf;

		urb = usb_alloc_urb(0, GFP_KERNEL);
		if (!urb)
			return -ENOMEM;

		buf = kmalloc(dev->rx_buf_sz, GFP_KERNEL);
		if (!buf) {
			usb_free_urb(urb);
			return -ENOMEM;
		}

		usb_fill_bulk_urb(urb, dev->udev, dev->pipe_in, buf,
				  dev->rx_buf_sz, vkgs_usb_read_bulk_callback,
				  dev);
		urb->transfer_flags |= URB_FREE_BUFFER;
		usb_anchor_urb(urb, &dev->rx_submitted);

		ret = usb_submit_urb(urb, GFP_KERNEL);
		if (ret) {
			usb_unanchor_urb(urb);
			usb_free_urb(urb);
			netdev_err(ndev, "rx urb submit failed: %d\n", ret);
			return ret;
		}
		usb_free_urb(urb);
	}
	return 0;
}

static int vkgs_usb_open(struct net_device *netdev)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	int ret;

	ret = open_candev(netdev);
	if (ret)
		return ret;

	if (dev->can.ctrlmode & CAN_CTRLMODE_FD)
		dev->hf_size_tx = VKGS_USB_HF_HDR_LEN + 64;
	else
		dev->hf_size_tx = VKGS_USB_HF_HDR_LEN + 8;

	vkgs_usb_init_tx_ctx(dev);
	atomic_set(&dev->active_tx_urbs, 0);
	ret = vkgs_usb_set_mode_cmd(dev, VKGS_USB_CAN_MODE_RESET, 0);
	if (ret)
		goto err_rx;
	msleep(VKGS_USB_MODE_SETTLE_MS);

	/* Rebuild the complete firmware configuration on every netdev open.
	 * SocketCAN may have sent timing while the interface was still down;
	 * MODE START must nevertheless remain the last control request here.
	 */
	ret = vkgs_usb_host_format(dev);
	if (ret)
		goto err_rx;
	ret = vkgs_usb_set_bittiming(netdev);
	if (ret)
		goto err_rx;
	if (dev->can.ctrlmode & CAN_CTRLMODE_FD) {
		ret = vkgs_usb_set_data_bittiming(netdev);
		if (ret)
			goto err_rx;
	}
	ret = vkgs_usb_set_bus_load_cmd(dev, false);
	if (ret)
		goto err_rx;
	ret = vkgs_usb_set_termination_cmd(dev, dev->termination);
	if (ret)
		goto err_rx;
	WRITE_ONCE(dev->rx_running, true);
	ret = vkgs_usb_alloc_rx_urbs(dev);
	if (ret)
		goto err_rx;

	dev->can.state = CAN_STATE_ERROR_ACTIVE;
	ret = vkgs_usb_set_mode_cmd(dev, VKGS_USB_CAN_MODE_START,
				     vkgs_usb_start_flags(dev));
	if (ret) {
		dev->can.state = CAN_STATE_STOPPED;
		goto err_rx;
	}

	netif_start_queue(netdev);
	return 0;

err_rx:
	WRITE_ONCE(dev->rx_running, false);
	usb_kill_anchored_urbs(&dev->rx_submitted);
	vkgs_usb_set_mode_cmd(dev, VKGS_USB_CAN_MODE_RESET, 0);
	vkgs_usb_host_format(dev);
	close_candev(netdev);
	return ret;
}

static int vkgs_usb_stop(struct net_device *netdev)
{
	struct vkgs_usb *dev = netdev_priv(netdev);

	netif_stop_queue(netdev);
	WRITE_ONCE(dev->rx_running, false);
	usb_kill_anchored_urbs(&dev->tx_submitted);
	vkgs_usb_set_mode_cmd(dev, VKGS_USB_CAN_MODE_RESET, 0);
	vkgs_usb_host_format(dev);
	atomic_set(&dev->active_tx_urbs, 0);
	usb_kill_anchored_urbs(&dev->rx_submitted);
	dev->can.state = CAN_STATE_STOPPED;
	close_candev(netdev);
	return 0;
}

static int vkgs_usb_set_mode(struct net_device *netdev, enum can_mode mode)
{
	struct vkgs_usb *dev = netdev_priv(netdev);

	switch (mode) {
	case CAN_MODE_START:
		dev->can.state = CAN_STATE_ERROR_ACTIVE;
		return vkgs_usb_set_mode_cmd(dev, VKGS_USB_CAN_MODE_START,
					      vkgs_usb_start_flags(dev));
	default:
		return -EOPNOTSUPP;
	}
}

/* rx/tx error counters are cached from the STATE/BERR event frames the
 * device pushes on its own (vkgs_usb_handle_state()/vkgs_usb_handle_berr()),
 * so this can return the cache instead of paying for a synchronous control
 * transfer on every call (this is on the hot path for e.g.
 * `ip -details -statistics link show`, which polls it).
 */
static int vkgs_usb_get_berr_counter(const struct net_device *netdev,
				      struct can_berr_counter *bec)
{
	struct vkgs_usb *dev = netdev_priv(netdev);

	*bec = dev->bec;
	return 0;
}

static int vkgs_usb_set_phys_id(struct net_device *netdev,
				 enum ethtool_phys_id_state state)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	struct vkgs_usb_identify_mode imode;

	switch (state) {
	case ETHTOOL_ID_ACTIVE:
		imode.mode = cpu_to_le32(1);
		break;
	case ETHTOOL_ID_INACTIVE:
		imode.mode = cpu_to_le32(0);
		break;
	default:
		/* The firmware blinks on its own once started (mode=1 above),
		 * so no per-tick ON/OFF toggling is needed here.
		 */
		return 0;
	}

	return vkgs_usb_send(dev, VKGS_USB_BREQ_IDENTIFY, &imode, sizeof(imode));
}

static const struct ethtool_ops vkgs_usb_ethtool_ops = {
	.set_phys_id = vkgs_usb_set_phys_id,
};

static const u16 vkgs_usb_termination_const[] = {
	VKGS_USB_TERMINATION_OFF,
	VKGS_USB_TERMINATION_ON,
};

static int vkgs_usb_set_termination(struct net_device *netdev, u16 term)
{
	struct vkgs_usb *dev = netdev_priv(netdev);
	bool enable = term == VKGS_USB_TERMINATION_ON;
	int ret;

	ret = vkgs_usb_set_termination_cmd(dev, enable);
	if (!ret)
		dev->termination = enable;
	return ret;
}

static const struct net_device_ops vkgs_usb_netdev_ops = {
	.ndo_open = vkgs_usb_open,
	.ndo_stop = vkgs_usb_stop,
	.ndo_start_xmit = vkgs_usb_start_xmit,
	.ndo_change_mtu = can_change_mtu,
};

/* ---- probe / disconnect ----------------------------------------------- */

static void vkgs_usb_load_bt_const(struct can_bittiming_const *out,
				    const struct vkgs_usb_bt_const_fields *in)
{
	strscpy(out->name, VKGS_USB_DRIVER_NAME, sizeof(out->name));
	out->tseg1_min = le32_to_cpu(in->tseg1_min);
	out->tseg1_max = le32_to_cpu(in->tseg1_max);
	out->tseg2_min = le32_to_cpu(in->tseg2_min);
	out->tseg2_max = le32_to_cpu(in->tseg2_max);
	out->sjw_max = le32_to_cpu(in->sjw_max);
	out->brp_min = le32_to_cpu(in->brp_min);
	out->brp_max = le32_to_cpu(in->brp_max);
	out->brp_inc = le32_to_cpu(in->brp_inc);
}

static void vkgs_usb_log_device_info(struct vkgs_usb *dev,
				     const struct vkgs_usb_bsp_device_info *info)
{
	u32 sw_version;
	u32 hw_version;

	sw_version = le32_to_cpu(info->sw_version);
	hw_version = le32_to_cpu(info->hw_version);

	dev_info(&dev->intf->dev,
		 "ch%u device info: sw=v%u.%u.%u "
		 "hw=v%u.%u isolated=%u usb=%s\n",
		 dev->channel,
		 (sw_version >> 16) & 0xff, (sw_version >> 8) & 0xff,
		 sw_version & 0xff,
		 (hw_version >> 8) & 0xff, hw_version & 0xff,
		 !!((hw_version >> 24) & 0x01),
		 dev->udev->speed == USB_SPEED_HIGH ? "HS" :
		 dev->udev->speed == USB_SPEED_FULL ? "FS" : "other");
}

static int vkgs_usb_probe(struct usb_interface *intf,
			   const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct usb_endpoint_descriptor *ep_in, *ep_out;
	struct vkgs_usb_bsp_device_info info;
	struct vkgs_usb_bt_const bt_const;
	struct net_device *netdev;
	struct vkgs_usb *dev;
	bool have_info = false;
	bool term = false;
	u32 feature;
	int ret;

	ret = usb_find_common_endpoints(intf->cur_altsetting, &ep_in, &ep_out,
					NULL, NULL);
	if (ret) {
		dev_err(&intf->dev, "no bulk endpoints found\n");
		return ret;
	}

	netdev = alloc_candev(sizeof(struct vkgs_usb), VKGS_USB_MAX_TX_URBS);
	if (!netdev)
		return -ENOMEM;

	dev = netdev_priv(netdev);
	dev->netdev = netdev;
	dev->udev = udev;
	dev->intf = intf;
	dev->channel = intf->cur_altsetting->desc.bInterfaceNumber;
	dev->pipe_in = usb_rcvbulkpipe(udev, ep_in->bEndpointAddress);
	dev->pipe_out = usb_sndbulkpipe(udev, ep_out->bEndpointAddress);

	init_usb_anchor(&dev->tx_submitted);
	init_usb_anchor(&dev->rx_submitted);
	spin_lock_init(&dev->tx_ctx_lock);
	spin_lock_init(&dev->stats_lock);
	vkgs_usb_init_tx_ctx(dev);

	usb_set_intfdata(intf, dev);
	SET_NETDEV_DEV(netdev, &intf->dev);
	netdev->netdev_ops = &vkgs_usb_netdev_ops;
	netdev->flags |= IFF_ECHO;
	netdev->dev_id = dev->channel;

	/* Tell the device the host is little-endian, then read its constants. */
	ret = vkgs_usb_host_format(dev);
	if (ret) {
		dev_err(&intf->dev, "host format failed: %d\n", ret);
		goto err_free;
	}
	ret = vkgs_usb_get_device_info(dev, &info);
	if (ret)
		dev_dbg(&intf->dev, "optional device info unavailable: %d\n",
			ret);
	else
		have_info = true;

	ret = vkgs_usb_recv(dev, VKGS_USB_BREQ_BT_CONST, 0, &bt_const,
			     sizeof(bt_const));
	if (ret) {
		dev_err(&intf->dev, "bt_const read failed: %d\n", ret);
		goto err_free;
	}

	feature = le32_to_cpu(bt_const.feature);
	dev->feature = feature;
	if (feature & VKGS_USB_FEATURE_IDENTIFY)
		netdev->ethtool_ops = &vkgs_usb_ethtool_ops;
	vkgs_usb_load_bt_const(&dev->bt_const, &bt_const.btc);

	dev->can.clock.freq = le32_to_cpu(bt_const.fclk_can);
	if (!dev->can.clock.freq)
		dev->can.clock.freq = VKGS_USB_CLOCK_HZ;
	dev->can.bittiming_const = &dev->bt_const;
	dev->can.do_set_bittiming = vkgs_usb_set_bittiming;
	dev->can.do_set_mode = vkgs_usb_set_mode;
	dev->can.do_get_berr_counter = vkgs_usb_get_berr_counter;

	dev->can.ctrlmode_supported = 0;
	if (feature & VKGS_USB_FEATURE_LISTEN_ONLY)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_LISTENONLY;
	if (feature & VKGS_USB_FEATURE_LOOP_BACK)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_LOOPBACK;
	if (feature & VKGS_USB_FEATURE_TRIPLE_SAMPLE)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_3_SAMPLES;
	if (feature & VKGS_USB_FEATURE_ONE_SHOT)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_ONE_SHOT;
	if (feature & VKGS_USB_FEATURE_BERR_REPORTING)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_BERR_REPORTING;

	if (feature & VKGS_USB_FEATURE_FD) {
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_FD;
#ifdef CAN_CTRLMODE_FD_NON_ISO
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_FD_NON_ISO;
#endif
		dev->can.data_bittiming_const = &dev->bt_const;
		dev->can.do_set_data_bittiming = vkgs_usb_set_data_bittiming;

		if (feature & VKGS_USB_FEATURE_BT_CONST_EXT) {
			struct vkgs_usb_bt_const_ext ext;

			ret = vkgs_usb_recv(dev, VKGS_USB_BREQ_BT_CONST_EXT, 0,
					     &ext, sizeof(ext));
			if (ret) {
				dev_err(&intf->dev,
					"bt_const_ext read failed: %d\n", ret);
				goto err_free;
			}
			vkgs_usb_load_bt_const(&dev->data_bt_const, &ext.dbtc);
			dev->can.data_bittiming_const = &dev->data_bt_const;
		}
	}

	/* Termination via the private CAN_TERMINATION request. */
	if (!vkgs_usb_get_termination(dev, &term))
		dev->termination = term;
	dev->can.termination_const = vkgs_usb_termination_const;
	dev->can.termination_const_cnt = ARRAY_SIZE(vkgs_usb_termination_const);
	dev->can.termination = dev->termination ? VKGS_USB_TERMINATION_ON :
						  VKGS_USB_TERMINATION_OFF;
	dev->can.do_set_termination = vkgs_usb_set_termination;

	dev->rx_buf_sz = ALIGN(VKGS_USB_HF_HDR_LEN + 64,
			       usb_maxpacket(udev, dev->pipe_in));

	netdev->min_mtu = CAN_MTU;
	netdev->max_mtu = (feature & VKGS_USB_FEATURE_FD) ? CANFD_MTU : CAN_MTU;

	ret = register_candev(netdev);
	if (ret) {
		dev_err(&intf->dev, "register_candev failed: %d\n", ret);
		goto err_free;
	}

	if (have_info)
		vkgs_usb_log_device_info(dev, &info);
	dev_info(&intf->dev, "VKGS USB channel %u registered as %s\n",
		 dev->channel, netdev->name);
	return 0;

err_free:
	usb_set_intfdata(intf, NULL);
	free_candev(netdev);
	return ret;
}

static void vkgs_usb_disconnect(struct usb_interface *intf)
{
	struct vkgs_usb *dev = usb_get_intfdata(intf);

	usb_set_intfdata(intf, NULL);
	if (!dev)
		return;

	netif_device_detach(dev->netdev);
	WRITE_ONCE(dev->rx_running, false);
	unregister_candev(dev->netdev);
	usb_kill_anchored_urbs(&dev->tx_submitted);
	usb_kill_anchored_urbs(&dev->rx_submitted);
	free_candev(dev->netdev);
}

static const struct usb_device_id vkgs_usb_table[] = {
	{ USB_DEVICE(VKGS_USB_VID, VKGS_USB_PID) },
	{}
};
MODULE_DEVICE_TABLE(usb, vkgs_usb_table);

static struct usb_driver vkgs_usb_driver = {
	.name = VKGS_USB_DRIVER_NAME,
	.probe = vkgs_usb_probe,
	.disconnect = vkgs_usb_disconnect,
	.id_table = vkgs_usb_table,
};

module_usb_driver(vkgs_usb_driver);

MODULE_AUTHOR("VEK");
MODULE_DESCRIPTION("VKGS USB (candleLight-compatible) USB CAN-FD driver");
MODULE_VERSION(VKGS_USB_DRV_VERSION);
MODULE_LICENSE("GPL");
