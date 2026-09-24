// SPDX-License-Identifier: GPL-2.0
/*
 * VCAN USB SocketCAN driver.
 *
 * Out-of-tree driver for the VCAN device firmware. The
 * device is a USB-CAN(FD) adapter that exposes ONE USB interface per CAN
 * channel (each with its own bulk endpoint pair) and speaks the self-describing
 * VCAN frame protocol: every frame starts with { echo_id, opcode, flags } where
 * opcode = (channel << 12) | byte_size.
 *
 * Design notes (verified against the firmware):
 *   - One netdev per USB interface; the driver probes per interface and follows
 *     the device's interface count instead of hard-coding it.
 *   - The firmware does NOT echo transmitted frames, so TX completion happens on
 *     the bulk-OUT URB completion.
 *   - Bit timing is register-encoded: the firmware adds 1 to every segment and
 *     ignores prop_seg, so the host sends brp-1, (prop_seg+phase_seg1)-1,
 *     phase_seg2-1, sjw-1.
 *   - Termination and bus-load reporting use the HAL-specific requests;
 *     version/UID/UUID are read
 *     with the HAL-specific VCAN_USB_BREQ_BSP_DEVICE_INFO request.
 *   - HW timestamps are not used (RX frames still carry a device timestamp in
 *     the header, but it is ignored here), keeping the receive path portable.
 *     Compatibility helpers support upstream kernels from 4.12 onward.
 */

#include <linux/can.h>
#include <linux/can/dev.h>
#include <linux/can/error.h>
#include <linux/delay.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/netdevice.h>
#include <linux/slab.h>
#include <linux/usb.h>

#include "vcan_usb.h"
#include "usbcan_compat.h"

#define VCAN_USB_DRV_VERSION  "1.1.4"
#define VCAN_USB_MAX_TX_URBS  10
#define VCAN_USB_MAX_RX_URBS  30
#define VCAN_USB_CTRL_TIMEOUT 1000
#define VCAN_USB_MODE_SETTLE_MS 150

struct vcan_usb;

struct vcan_usb_tx_ctx {
	struct vcan_usb *dev;
	u32 echo_id;
	u8 data_len;
};

struct vcan_usb {
	struct can_priv can; /* must be first */
	struct net_device *netdev;
	struct usb_device *udev;
	struct usb_interface *intf;

	u8 channel;
	u32 feature;
	unsigned int pipe_in;
	unsigned int pipe_out;
	unsigned int rx_buf_sz;

	struct can_bittiming_const bt_const;
	struct can_bittiming_const data_bt_const;

	struct usb_anchor tx_submitted;
	struct usb_anchor rx_submitted;
	atomic_t active_tx_urbs;
	spinlock_t tx_ctx_lock;
	spinlock_t stats_lock;
	struct vcan_usb_tx_ctx tx_context[VCAN_USB_MAX_TX_URBS];

	struct can_berr_counter bec;
	bool termination;
	bool rx_running;
};

static void vcan_usb_account_rx(struct vcan_usb *dev, unsigned int len,
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

static void vcan_usb_account_tx(struct vcan_usb *dev, unsigned int len)
{
	unsigned long flags;

	spin_lock_irqsave(&dev->stats_lock, flags);
	dev->netdev->stats.tx_packets++;
	dev->netdev->stats.tx_bytes += len;
	spin_unlock_irqrestore(&dev->stats_lock, flags);
}

/* ---- TX context bookkeeping ------------------------------------------- */

static struct vcan_usb_tx_ctx *vcan_usb_alloc_tx_ctx(struct vcan_usb *dev)
{
	unsigned long flags;
	int i;

	spin_lock_irqsave(&dev->tx_ctx_lock, flags);
	for (i = 0; i < VCAN_USB_MAX_TX_URBS; i++) {
		if (dev->tx_context[i].echo_id == VCAN_USB_MAX_TX_URBS) {
			dev->tx_context[i].echo_id = i;
			spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
			return &dev->tx_context[i];
		}
	}
	spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
	return NULL;
}

static void vcan_usb_free_tx_ctx(struct vcan_usb_tx_ctx *ctx)
{
	struct vcan_usb *dev = ctx->dev;
	unsigned long flags;

	spin_lock_irqsave(&dev->tx_ctx_lock, flags);
	ctx->echo_id = VCAN_USB_MAX_TX_URBS;
	spin_unlock_irqrestore(&dev->tx_ctx_lock, flags);
}

static void vcan_usb_init_tx_ctx(struct vcan_usb *dev)
{
	int i;

	for (i = 0; i < VCAN_USB_MAX_TX_URBS; i++) {
		dev->tx_context[i].dev = dev;
		dev->tx_context[i].echo_id = VCAN_USB_MAX_TX_URBS;
	}
}

/* ---- control transfers ------------------------------------------------- */

static void vcan_usb_fill_hdr(struct vcan_usb_hdr *hdr, u8 channel, u8 request,
			      u16 size)
{
	hdr->echo_id = cpu_to_le32(VCAN_USB_ECHO_SETUP);
	hdr->opcode = cpu_to_le16(VCAN_USB_OPCODE(channel, size));
	hdr->flags = cpu_to_le16(request);
}

/* SET: caller fills the payload fields, this fills the header and sends it. */
static int vcan_usb_send(struct vcan_usb *dev, u8 request, void *buf, u16 len)
{
	void *dma;
	int ret;

	vcan_usb_fill_hdr((struct vcan_usb_hdr *)buf, dev->channel, request, len);

	dma = kmemdup(buf, len, GFP_KERNEL);
	if (!dma)
		return -ENOMEM;

	ret = usb_control_msg(dev->udev, usb_sndctrlpipe(dev->udev, 0),
			      request,
			      USB_DIR_OUT | USB_TYPE_VENDOR | USB_RECIP_INTERFACE,
			      0, dev->channel, dma, len, VCAN_USB_CTRL_TIMEOUT);
	kfree(dma);
	return ret < 0 ? ret : (ret == len ? 0 : -EIO);
}

/* GET: the device fills the buffer (including the header). */
static int vcan_usb_recv(struct vcan_usb *dev, u8 request, void *buf, u16 len)
{
	void *dma;
	int ret;

	dma = kzalloc(len, GFP_KERNEL);
	if (!dma)
		return -ENOMEM;

	ret = usb_control_msg(dev->udev, usb_rcvctrlpipe(dev->udev, 0),
			      request | VCAN_USB_BREQ_DIR_READ,
			      USB_DIR_IN | USB_TYPE_VENDOR | USB_RECIP_INTERFACE,
			      0, dev->channel, dma, len, VCAN_USB_CTRL_TIMEOUT);
	if (ret >= 0)
		memcpy(buf, dma, len);
	kfree(dma);
	return ret < 0 ? ret : (ret == len ? 0 : -EIO);
}

static int vcan_usb_set_mode_cmd(struct vcan_usb *dev, u32 mode, u32 flags)
{
	struct vcan_usb_device_mode dm = {
		.mode = cpu_to_le32(mode),
		.mode_flags = cpu_to_le32(flags),
	};

	return vcan_usb_send(dev, VCAN_USB_BREQ_MODE, &dm, sizeof(dm));
}

static int vcan_usb_host_format(struct vcan_usb *dev)
{
	struct vcan_usb_host_config hconf = {
		.byte_order = cpu_to_le32(0x0000beef),
	};

	return vcan_usb_send(dev, VCAN_USB_BREQ_HOST_FORMAT, &hconf,
			     sizeof(hconf));
}

static int vcan_usb_get_device_info(struct vcan_usb *dev,
				    struct vcan_usb_bsp_device_info *info)
{
	return vcan_usb_recv(dev, VCAN_USB_BREQ_BSP_DEVICE_INFO, info,
			     sizeof(*info));
}

static u32 vcan_usb_start_flags(struct vcan_usb *dev)
{
	u32 ctrlmode = dev->can.ctrlmode;
	u32 flags = 0;

	if (ctrlmode & CAN_CTRLMODE_LOOPBACK)
		flags |= VCAN_USB_MODE_LOOP_BACK;
	if (ctrlmode & CAN_CTRLMODE_LISTENONLY)
		flags |= VCAN_USB_MODE_LISTEN_ONLY;
	if (ctrlmode & CAN_CTRLMODE_3_SAMPLES)
		flags |= VCAN_USB_MODE_TRIPLE_SAMPLE;
	if (ctrlmode & CAN_CTRLMODE_ONE_SHOT)
		flags |= VCAN_USB_MODE_ONE_SHOT;
	if (ctrlmode & CAN_CTRLMODE_FD)
		flags |= VCAN_USB_MODE_FD;
#ifdef CAN_CTRLMODE_FD_NON_ISO
	if (ctrlmode & CAN_CTRLMODE_FD_NON_ISO)
		flags |= VCAN_USB_MODE_FD_NON_ISO;
#endif
	if (ctrlmode & CAN_CTRLMODE_BERR_REPORTING)
		flags |= VCAN_USB_MODE_BERR_REPORTING;
	return flags;
}

/* Firmware adds 1 to every segment and ignores prop_seg (register-encoded
 * timing), so fold prop_seg into phase_seg1 and subtract 1 from each field.
 */
static void vcan_usb_fill_bittiming(struct vcan_usb_device_bittiming *dbt,
				    const struct can_bittiming *bt)
{
	dbt->prop_seg = cpu_to_le32(0);
	dbt->phase_seg1 = cpu_to_le32(bt->prop_seg + bt->phase_seg1 - 1);
	dbt->phase_seg2 = cpu_to_le32(bt->phase_seg2 - 1);
	dbt->sjw = cpu_to_le32(bt->sjw - 1);
	dbt->brp = cpu_to_le32(bt->brp - 1);
}

static int vcan_usb_set_bittiming(struct net_device *netdev)
{
	struct vcan_usb *dev = netdev_priv(netdev);
	struct vcan_usb_device_bittiming dbt = { 0 };

	vcan_usb_fill_bittiming(&dbt, &dev->can.bittiming);
	return vcan_usb_send(dev, VCAN_USB_BREQ_BITTIMING, &dbt, sizeof(dbt));
}

static int vcan_usb_set_data_bittiming(struct net_device *netdev)
{
	struct vcan_usb *dev = netdev_priv(netdev);
	struct vcan_usb_device_bittiming dbt = { 0 };

	vcan_usb_fill_bittiming(&dbt, &dev->can.data_bittiming);
	return vcan_usb_send(dev, VCAN_USB_BREQ_DATA_BITTIMING, &dbt,
			     sizeof(dbt));
}

static int vcan_usb_set_termination_cmd(struct vcan_usb *dev, bool on)
{
	struct vcan_usb_can_config cfg = {
		.state = cpu_to_le32(on ? 1 : 0),
	};

	return vcan_usb_send(dev, VCAN_USB_BREQ_CAN_TERMINATION, &cfg,
			     sizeof(cfg));
}

static int vcan_usb_set_bus_load_cmd(struct vcan_usb *dev, bool on)
{
	struct vcan_usb_can_config cfg = {
		.state = cpu_to_le32(on ? 1 : 0),
	};

	return vcan_usb_send(dev, VCAN_USB_BREQ_CAN_BUS_LOAD, &cfg,
			     sizeof(cfg));
}

static int vcan_usb_get_termination(struct vcan_usb *dev, bool *on)
{
	struct vcan_usb_can_config cfg;
	int ret;

	ret = vcan_usb_recv(dev, VCAN_USB_BREQ_CAN_TERMINATION, &cfg,
			    sizeof(cfg));
	if (ret)
		return ret;
	*on = le32_to_cpu(cfg.state) != 0;
	return 0;
}

/* ---- RX / event handling ---------------------------------------------- */

static void vcan_usb_set_state(struct vcan_usb *dev, u32 dev_state)
{
	enum can_state old = dev->can.state;
	enum can_state new;

	switch (dev_state) {
	case VCAN_USB_CAN_STATE_BUS_OFF:
		new = CAN_STATE_BUS_OFF;
		break;
	case VCAN_USB_CAN_STATE_ERROR_PASSIVE:
		new = CAN_STATE_ERROR_PASSIVE;
		break;
	case VCAN_USB_CAN_STATE_ERROR_WARNING:
		new = CAN_STATE_ERROR_WARNING;
		break;
	case VCAN_USB_CAN_STATE_ERROR_ACTIVE:
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
		can_bus_off(dev->netdev);
	} else if (new == CAN_STATE_ERROR_WARNING) {
		dev->can.can_stats.error_warning++;
	} else if (new == CAN_STATE_ERROR_PASSIVE) {
		dev->can.can_stats.error_passive++;
	}
}

static void vcan_usb_handle_state(struct vcan_usb *dev,
				  const struct vcan_usb_device_state *st)
{
	struct net_device *ndev = dev->netdev;
	u32 state = le32_to_cpu(st->state);
	struct can_frame *cf;
	struct sk_buff *skb;

	dev->bec.rxerr = le32_to_cpu(st->rxerr);
	dev->bec.txerr = le32_to_cpu(st->txerr);
	vcan_usb_set_state(dev, state);

	/* ERROR_ACTIVE is the normal operating state, not a CAN error.  State
	 * recovery and duplicate STOPPED/ACTIVE notifications must not be exposed
	 * as empty CAN_ERR_FLAG frames to SocketCAN applications.
	 */
	if (state != VCAN_USB_CAN_STATE_BUS_OFF &&
	    state != VCAN_USB_CAN_STATE_ERROR_PASSIVE &&
	    state != VCAN_USB_CAN_STATE_ERROR_WARNING)
		return;

	skb = alloc_can_err_skb(ndev, &cf);
	if (!skb)
		return;

	if (state == VCAN_USB_CAN_STATE_BUS_OFF) {
		cf->can_id |= CAN_ERR_BUSOFF;
	} else if (state == VCAN_USB_CAN_STATE_ERROR_PASSIVE) {
		cf->can_id |= CAN_ERR_CRTL;
		if (dev->bec.rxerr >= 128)
			cf->data[1] |= CAN_ERR_CRTL_RX_PASSIVE;
		if (dev->bec.txerr >= 128)
			cf->data[1] |= CAN_ERR_CRTL_TX_PASSIVE;
	} else if (state == VCAN_USB_CAN_STATE_ERROR_WARNING) {
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
 * hal_can_error_code_t (see hal_can_reg.h). Only emitted when the user has
 * opted in via CAN_CTRLMODE_BERR_REPORTING; rx/tx error counters are always
 * cached regardless, since do_get_berr_counter() relies on them.
 */
static void vcan_usb_handle_berr(struct vcan_usb *dev,
				 const struct vcan_usb_device_berr *be)
{
	struct net_device *ndev = dev->netdev;
	struct can_frame *cf;
	struct sk_buff *skb;

	dev->bec.rxerr = be->rx_error_count;
	dev->bec.txerr = be->tx_error_count;

	if (!(dev->can.ctrlmode & CAN_CTRLMODE_BERR_REPORTING))
		return;
	/* Firmware may send a BERR snapshot while the controller recovers.  NONE
	 * and NO_CHANGE carry counters only and are not protocol violations.
	 */
	if (be->error_code == VCAN_USB_ERROR_CODE_NONE ||
	    be->error_code == VCAN_USB_ERROR_CODE_NO_CHANGE)
		return;

	skb = alloc_can_err_skb(ndev, &cf);
	if (!skb)
		return;

	cf->can_id |= CAN_ERR_PROT;
	switch (be->error_code) {
	case VCAN_USB_ERROR_CODE_STUFF:
		cf->data[2] |= CAN_ERR_PROT_STUFF;
		break;
	case VCAN_USB_ERROR_CODE_FORM:
		cf->data[2] |= CAN_ERR_PROT_FORM;
		break;
	case VCAN_USB_ERROR_CODE_ACK:
		cf->can_id |= CAN_ERR_ACK;
		cf->data[3] = CAN_ERR_PROT_LOC_ACK;
		break;
	case VCAN_USB_ERROR_CODE_BIT1:
		cf->data[2] |= CAN_ERR_PROT_BIT1;
		break;
	case VCAN_USB_ERROR_CODE_BIT0:
		cf->data[2] |= CAN_ERR_PROT_BIT0;
		break;
	case VCAN_USB_ERROR_CODE_CRC:
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

static void vcan_usb_handle_rx_frame(struct vcan_usb *dev,
				     const struct vcan_usb_host_frame *hf,
				     u16 size)
{
	struct net_device *ndev = dev->netdev;
	u16 flags = le16_to_cpu(hf->hdr.flags);
	u32 raw_id;
	bool is_fd = flags & VCAN_USB_FLAG_FD;
	struct canfd_frame *cfd;
	struct can_frame *cf;
	struct sk_buff *skb;
	unsigned int len;

	if (size < VCAN_USB_FRAME_DATA_OFFSET) {
		ndev->stats.rx_length_errors++;
		ndev->stats.rx_dropped++;
		return;
	}
	raw_id = le32_to_cpu(hf->can_id);

	if (is_fd && (flags & VCAN_USB_FLAG_RTR)) {
		ndev->stats.rx_frame_errors++;
		ndev->stats.rx_dropped++;
		return;
	}

	if (is_fd) {
		len = usbcan_fd_dlc2len(hf->dlc & 0x0f);
		if (size < VCAN_USB_FRAME_DATA_OFFSET + len) {
			ndev->stats.rx_length_errors++;
			ndev->stats.rx_dropped++;
			return;
		}
		skb = alloc_canfd_skb(ndev, &cfd);
		if (!skb) {
			ndev->stats.rx_dropped++;
			return;
		}
		cfd->len = len;
		cfd->can_id = (flags & VCAN_USB_FLAG_EFF) ?
			(raw_id & VCAN_USB_ID_MASK_EXT) | CAN_EFF_FLAG :
			raw_id & VCAN_USB_ID_MASK_STD;
		if (flags & VCAN_USB_FLAG_ERR)
			cfd->can_id |= CAN_ERR_FLAG;
		if (flags & VCAN_USB_FLAG_BRS)
			cfd->flags |= CANFD_BRS;
		if (flags & VCAN_USB_FLAG_ESI)
			cfd->flags |= CANFD_ESI;
		memcpy(cfd->data, hf->data, cfd->len);
		len = cfd->len;
	} else {
		len = usbcan_cc_dlc2len(hf->dlc & 0x0f);
		if (!(flags & VCAN_USB_FLAG_RTR) &&
		    size < VCAN_USB_FRAME_DATA_OFFSET + len) {
			ndev->stats.rx_length_errors++;
			ndev->stats.rx_dropped++;
			return;
		}
		skb = alloc_can_skb(ndev, &cf);
		if (!skb) {
			ndev->stats.rx_dropped++;
			return;
		}
		cf->can_id = (flags & VCAN_USB_FLAG_EFF) ?
			(raw_id & VCAN_USB_ID_MASK_EXT) | CAN_EFF_FLAG :
			raw_id & VCAN_USB_ID_MASK_STD;
		if (flags & VCAN_USB_FLAG_RTR)
			cf->can_id |= CAN_RTR_FLAG;
		if (flags & VCAN_USB_FLAG_ERR)
			cf->can_id |= CAN_ERR_FLAG;
		cf->len = len;
		if (!(cf->can_id & CAN_RTR_FLAG))
			memcpy(cf->data, hf->data, cf->len);
		len = (cf->can_id & CAN_RTR_FLAG) ? 0 : cf->len;
	}

	vcan_usb_account_rx(dev, len, flags & VCAN_USB_FLAG_OVERFLOW);
	netif_rx(skb);
}

/* Parse one (possibly coalesced) bulk-IN transfer. The VCAN protocol carries an
 * explicit per-frame size in opcode, so we validate it first and stride by it;
 * a single malformed/unknown frame is skipped (counted) without losing the rest
 * of the bundle, and a length error stops the bundle to avoid an over-read.
 */
static void vcan_usb_parse_bulk(struct vcan_usb *dev, const u8 *buf, int len)
{
	struct net_device *ndev = dev->netdev;
	int off = 0;

	while (off + VCAN_USB_HDR_LEN <= len) {
		const struct vcan_usb_hdr *hdr =
			(const struct vcan_usb_hdr *)(buf + off);
		u16 opcode = le16_to_cpu(hdr->opcode);
		u32 echo_id = le32_to_cpu(hdr->echo_id);
		u16 flags = le16_to_cpu(hdr->flags);
		u16 size = VCAN_USB_OPCODE_SIZE(opcode);

		/* Firmware appends a four-byte zero sentinel, then rounds the
		 * transfer up to the USB packet size.  Bytes after the sentinel
		 * are padding and may contain stale FIFO data, so do not try to
		 * parse them as another protocol frame.
		 */
		if (!echo_id)
			break;

		/* opcode size is authoritative; an out-of-range size means the
		 * stream is corrupt and we can no longer trust the stride.
		 */
		if (size < VCAN_USB_HDR_LEN || off + size > len) {
			ndev->stats.rx_length_errors++;
			break;
		}

		/* every frame on this interface's IN endpoint must carry this
		 * channel in its opcode; a mismatch means a corrupt opcode, so
		 * the size cannot be trusted either -> stop the bundle.
		 */
		if (VCAN_USB_OPCODE_CHANNEL(opcode) != dev->channel) {
			ndev->stats.rx_frame_errors++;
			netdev_dbg(ndev,
				   "rx opcode channel %u != %u, dropping bundle\n",
				   VCAN_USB_OPCODE_CHANNEL(opcode), dev->channel);
			break;
		}

		switch (echo_id) {
		case VCAN_USB_ECHO_RX:
			vcan_usb_handle_rx_frame(dev,
				(const struct vcan_usb_host_frame *)hdr, size);
			break;
		case VCAN_USB_ECHO_STATE:
			if (flags == VCAN_USB_BREQ_CAN_STATE &&
			    size >= sizeof(struct vcan_usb_device_state)) {
				vcan_usb_handle_state(dev,
					(const struct vcan_usb_device_state *)hdr);
			} else if (flags == VCAN_USB_BREQ_BERR &&
				   size >= sizeof(struct vcan_usb_device_berr)) {
				vcan_usb_handle_berr(dev,
					(const struct vcan_usb_device_berr *)hdr);
			}
			break;
		case VCAN_USB_ECHO_LOAD:
			break; /* bus-load telemetry not exposed */
		default:
			/* unknown marker but size/channel are sane: skip exactly
			 * this frame and resync on the next (length-prefix win).
			 */
			ndev->stats.rx_frame_errors++;
			netdev_dbg(ndev, "rx unknown echo_id 0x%08x, skipping\n",
				   echo_id);
			break;
		}
		off += size;
	}
}

static void vcan_usb_read_bulk_callback(struct urb *urb)
{
	struct vcan_usb *dev = urb->context;
	struct net_device *ndev = dev->netdev;
	int ret;

	switch (urb->status) {
	case 0:
		if (READ_ONCE(dev->rx_running) && netif_running(ndev) &&
		    netif_device_present(ndev))
			vcan_usb_parse_bulk(dev, urb->transfer_buffer,
					    urb->actual_length);
		break;
	case -ENOENT:
	case -ECONNRESET:
	case -ESHUTDOWN:
		return;
	default:
		ndev->stats.rx_errors++;
		break;
	}

	if (!READ_ONCE(dev->rx_running) || !netif_device_present(ndev))
		return;

	usb_fill_bulk_urb(urb, dev->udev, dev->pipe_in, urb->transfer_buffer,
			  dev->rx_buf_sz, vcan_usb_read_bulk_callback, dev);
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

static void vcan_usb_write_bulk_callback(struct urb *urb)
{
	struct vcan_usb_tx_ctx *ctx = urb->context;
	struct vcan_usb *dev = ctx->dev;
	struct net_device *ndev = dev->netdev;
	unsigned int idx = ctx->echo_id;
	unsigned int echo_len;

	switch (urb->status) {
	case 0:
		/* The firmware sends no TX echo, so complete here. */
		echo_len = usbcan_get_echo_skb(ndev, idx);
		vcan_usb_account_tx(dev, ctx->data_len);
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

	vcan_usb_free_tx_ctx(ctx);
	/* Full barrier pairs with the stop/recheck in start_xmit(). */
	atomic_dec_return(&dev->active_tx_urbs);
	if (netif_running(ndev) && netif_device_present(ndev) &&
	    netif_carrier_ok(ndev) && netif_queue_stopped(ndev))
		netif_wake_queue(ndev);
}

static netdev_tx_t vcan_usb_start_xmit(struct sk_buff *skb,
				       struct net_device *netdev)
{
	struct vcan_usb *dev = netdev_priv(netdev);
	struct net_device_stats *stats = &netdev->stats;
	struct vcan_usb_host_frame *hf;
	struct vcan_usb_tx_ctx *ctx;
	struct canfd_frame *cfd;
	struct can_frame *cf;
	unsigned int data_len;
	unsigned int wire_data_len;
	unsigned int frame_len;
	u16 fl = 0;
	u32 can_id;
	u32 raw_id;
	struct urb *urb;
	unsigned int idx;
	bool is_fd;
	int ret;

	if (can_dropped_invalid_skb(netdev, skb))
		return NETDEV_TX_OK;

	is_fd = can_is_canfd_skb(skb);
	if (is_fd) {
		cfd = (struct canfd_frame *)skb->data;
		can_id = cfd->can_id;
		data_len = cfd->len;
		fl |= VCAN_USB_FLAG_FD;
		if (cfd->flags & CANFD_BRS)
			fl |= VCAN_USB_FLAG_BRS;
		if (cfd->flags & CANFD_ESI)
			fl |= VCAN_USB_FLAG_ESI;
	} else {
		cf = (struct can_frame *)skb->data;
		can_id = cf->can_id;
		data_len = cf->len;
	}

	if (can_id & CAN_EFF_FLAG) {
		fl |= VCAN_USB_FLAG_EFF;
		raw_id = can_id & VCAN_USB_ID_MASK_EXT;
	} else {
		raw_id = can_id & VCAN_USB_ID_MASK_STD;
	}
	if (can_id & CAN_RTR_FLAG)
		fl |= VCAN_USB_FLAG_RTR;
	if (can_id & CAN_ERR_FLAG)
		fl |= VCAN_USB_FLAG_ERR;

	ctx = vcan_usb_alloc_tx_ctx(dev);
	if (!ctx) {
		/* Keep qdisc backpressure asserted until a completion releases a
		 * context.  Returning BUSY without stopping the queue can hot-loop.
		 */
		netif_stop_queue(netdev);
		smp_mb();
		/* Close the stop/completion race: a completion that ran immediately
		 * before stop_queue() could not observe the stopped queue to wake it.
		 */
		if (atomic_read(&dev->active_tx_urbs) < VCAN_USB_MAX_TX_URBS)
			netif_wake_queue(netdev);
		return NETDEV_TX_BUSY;
	}
	idx = ctx->echo_id;
	ctx->data_len = (can_id & CAN_RTR_FLAG) ? 0 : data_len;

	urb = usb_alloc_urb(0, GFP_ATOMIC);
	if (!urb)
		goto nomem_urb;

	/* The VCAN USB protocol uses a fixed payload area selected by the frame
	 * type, independent of the current DLC: 8 bytes for classic CAN and 64
	 * bytes for CAN FD.  Keep the unused tail zero-filled.  Besides matching
	 * firmware-generated IN frames, this lets the device safely decode the
	 * rounded CAN-FD DLC lengths (9..11 -> 12, 13..15 -> 16, etc.).
	 */
	wire_data_len = is_fd ? CANFD_MAX_DLEN : CAN_MAX_DLEN;
	frame_len = VCAN_USB_FRAME_DATA_OFFSET + wire_data_len;
	hf = kzalloc(frame_len, GFP_ATOMIC);
	if (!hf)
		goto nomem_hf;

	hf->hdr.echo_id = cpu_to_le32(VCAN_USB_ECHO_TX);
	hf->hdr.opcode = cpu_to_le16(VCAN_USB_OPCODE(dev->channel, frame_len));
	hf->hdr.flags = cpu_to_le16(fl);
	hf->can_id = cpu_to_le32(raw_id);
	hf->dlc = is_fd ? usbcan_fd_len2dlc(data_len) :
			    usbcan_get_cc_dlc(cf, dev->can.ctrlmode);
	if (!(can_id & CAN_RTR_FLAG))
		memcpy(hf->data, is_fd ? cfd->data : cf->data, data_len);

	usb_fill_bulk_urb(urb, dev->udev, dev->pipe_out, hf, frame_len,
			  vcan_usb_write_bulk_callback, ctx);
	urb->transfer_flags |= URB_FREE_BUFFER;
	usb_anchor_urb(urb, &dev->tx_submitted);

	ret = usbcan_put_echo_skb(skb, netdev, idx, data_len);
	if (unlikely(ret)) {
		usb_unanchor_urb(urb);
		usb_free_urb(urb);
		vcan_usb_free_tx_ctx(ctx);
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
		vcan_usb_free_tx_ctx(ctx);
		if (ret == -ENODEV) {
			netif_device_detach(netdev);
		} else {
			netdev_warn(netdev, "tx submit failed: %d\n", ret);
			stats->tx_dropped++;
		}
		return NETDEV_TX_OK;
	}

	usb_free_urb(urb);
	if (atomic_read(&dev->active_tx_urbs) >= VCAN_USB_MAX_TX_URBS) {
		netif_stop_queue(netdev);
		smp_mb();
		if (atomic_read(&dev->active_tx_urbs) < VCAN_USB_MAX_TX_URBS)
			netif_wake_queue(netdev);
	}
	return NETDEV_TX_OK;

nomem_hf:
	usb_free_urb(urb);
nomem_urb:
	vcan_usb_free_tx_ctx(ctx);
	dev_kfree_skb(skb);
	stats->tx_dropped++;
	return NETDEV_TX_OK;
}

/* ---- netdev ops -------------------------------------------------------- */

static int vcan_usb_alloc_rx_urbs(struct vcan_usb *dev)
{
	struct net_device *ndev = dev->netdev;
	int i, ret;

	for (i = 0; i < VCAN_USB_MAX_RX_URBS; i++) {
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
				  dev->rx_buf_sz, vcan_usb_read_bulk_callback,
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

static int vcan_usb_open(struct net_device *netdev)
{
	struct vcan_usb *dev = netdev_priv(netdev);
	int ret;

	ret = open_candev(netdev);
	if (ret)
		return ret;

	vcan_usb_init_tx_ctx(dev);
	atomic_set(&dev->active_tx_urbs, 0);
	ret = vcan_usb_set_mode_cmd(dev, VCAN_USB_CHANNEL_MODE_RESET, 0);
	if (ret)
		goto err_rx;
	msleep(VCAN_USB_MODE_SETTLE_MS);

	/* MODE must be the final control request in the initialization sequence.
	 * Re-send cached CAN-core timing here because SocketCAN normally programs
	 * it before ndo_open(), while this firmware requires HOST_FORMAT first.
	 */
	ret = vcan_usb_host_format(dev);
	if (ret)
		goto err_rx;
	ret = vcan_usb_set_bittiming(netdev);
	if (ret)
		goto err_rx;
	if (dev->can.ctrlmode & CAN_CTRLMODE_FD) {
		ret = vcan_usb_set_data_bittiming(netdev);
		if (ret)
			goto err_rx;
	}
	ret = vcan_usb_set_bus_load_cmd(dev, false);
	if (ret)
		goto err_rx;
	ret = vcan_usb_set_termination_cmd(dev, dev->termination);
	if (ret)
		goto err_rx;

	/* Arm reception only after stale device state has been reset and every
	 * control setting is committed. This prevents old queued frames from being
	 * delivered while ndo_open() is still configuring the controller.
	 */
	WRITE_ONCE(dev->rx_running, true);
	ret = vcan_usb_alloc_rx_urbs(dev);
	if (ret)
		goto err_rx;

	dev->can.state = CAN_STATE_ERROR_ACTIVE;
	ret = vcan_usb_set_mode_cmd(dev, VCAN_USB_CHANNEL_MODE_START,
				    vcan_usb_start_flags(dev));
	if (ret) {
		dev->can.state = CAN_STATE_STOPPED;
		goto err_rx;
	}

	netif_start_queue(netdev);
	return 0;

err_rx:
	WRITE_ONCE(dev->rx_running, false);
	usb_kill_anchored_urbs(&dev->rx_submitted);
	vcan_usb_set_mode_cmd(dev, VCAN_USB_CHANNEL_MODE_RESET, 0);
	vcan_usb_host_format(dev);
	close_candev(netdev);
	return ret;
}

static int vcan_usb_stop(struct net_device *netdev)
{
	struct vcan_usb *dev = netdev_priv(netdev);

	netif_stop_queue(netdev);
	WRITE_ONCE(dev->rx_running, false);
	usb_kill_anchored_urbs(&dev->tx_submitted);
	vcan_usb_set_mode_cmd(dev, VCAN_USB_CHANNEL_MODE_RESET, 0);
	vcan_usb_host_format(dev);
	atomic_set(&dev->active_tx_urbs, 0);
	usb_kill_anchored_urbs(&dev->rx_submitted);
	dev->can.state = CAN_STATE_STOPPED;
	close_candev(netdev);
	return 0;
}

static int vcan_usb_set_mode(struct net_device *netdev, enum can_mode mode)
{
	struct vcan_usb *dev = netdev_priv(netdev);

	switch (mode) {
	case CAN_MODE_START:
		dev->can.state = CAN_STATE_ERROR_ACTIVE;
		return vcan_usb_set_mode_cmd(dev, VCAN_USB_CHANNEL_MODE_START,
					     vcan_usb_start_flags(dev));
	default:
		return -EOPNOTSUPP;
	}
}

/* rx/tx error counters are cached from the STATE/BERR event frames the
 * device pushes on its own (vcan_usb_handle_state()/vcan_usb_handle_berr()),
 * so this can return the cache instead of paying for a synchronous control
 * transfer on every call (this is on the hot path for e.g.
 * `ip -details -statistics link show`, which polls it).
 */
static int vcan_usb_get_berr_counter(const struct net_device *netdev,
				     struct can_berr_counter *bec)
{
	struct vcan_usb *dev = netdev_priv(netdev);

	*bec = dev->bec;
	return 0;
}

static const u16 vcan_usb_termination_const[] = {
	VCAN_USB_TERMINATION_OFF,
	VCAN_USB_TERMINATION_ON,
};

static int vcan_usb_set_termination(struct net_device *netdev, u16 term)
{
	struct vcan_usb *dev = netdev_priv(netdev);
	bool enable = term == VCAN_USB_TERMINATION_ON;
	int ret;

	ret = vcan_usb_set_termination_cmd(dev, enable);
	if (!ret)
		dev->termination = enable;
	return ret;
}

static const struct net_device_ops vcan_usb_netdev_ops = {
	.ndo_open = vcan_usb_open,
	.ndo_stop = vcan_usb_stop,
	.ndo_start_xmit = vcan_usb_start_xmit,
	.ndo_change_mtu = can_change_mtu,
};

/* ---- probe / disconnect ----------------------------------------------- */

static void vcan_usb_load_bt_const(struct can_bittiming_const *out,
				   const struct vcan_usb_bt_const_fields *in)
{
	strscpy(out->name, VCAN_USB_DRIVER_NAME, sizeof(out->name));
	out->tseg1_min = le32_to_cpu(in->tseg1_min);
	out->tseg1_max = le32_to_cpu(in->tseg1_max);
	out->tseg2_min = le32_to_cpu(in->tseg2_min);
	out->tseg2_max = le32_to_cpu(in->tseg2_max);
	out->sjw_max = le32_to_cpu(in->sjw_max);
	out->brp_min = le32_to_cpu(in->brp_min);
	out->brp_max = le32_to_cpu(in->brp_max);
	out->brp_inc = le32_to_cpu(in->brp_inc);
}

static void vcan_usb_log_device_info(struct vcan_usb *dev,
				     const struct vcan_usb_bsp_device_info *info)
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

static int vcan_usb_probe(struct usb_interface *intf,
			  const struct usb_device_id *id)
{
	struct usb_device *udev = interface_to_usbdev(intf);
	struct usb_endpoint_descriptor *ep_in, *ep_out;
	struct vcan_usb_bsp_device_info info;
	struct vcan_usb_bt_const bt_const;
	struct net_device *netdev;
	struct vcan_usb *dev;
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

	netdev = alloc_candev(sizeof(struct vcan_usb), VCAN_USB_MAX_TX_URBS);
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
	vcan_usb_init_tx_ctx(dev);

	usb_set_intfdata(intf, dev);
	SET_NETDEV_DEV(netdev, &intf->dev);
	netdev->netdev_ops = &vcan_usb_netdev_ops;
	netdev->flags |= IFF_ECHO;
	netdev->dev_id = dev->channel;

	ret = vcan_usb_host_format(dev);
	if (ret) {
		dev_err(&intf->dev, "host format failed: %d\n", ret);
		goto err_free;
	}
	ret = vcan_usb_get_device_info(dev, &info);
	if (ret)
		dev_dbg(&intf->dev, "optional device info unavailable: %d\n",
			ret);
	else
		have_info = true;

	ret = vcan_usb_recv(dev, VCAN_USB_BREQ_BT_CONST, &bt_const,
			    sizeof(bt_const));
	if (ret) {
		dev_err(&intf->dev, "bt_const read failed: %d\n", ret);
		goto err_free;
	}

	feature = le32_to_cpu(bt_const.feature);
	dev->feature = feature;
	vcan_usb_load_bt_const(&dev->bt_const, &bt_const.btc);

	dev->can.clock.freq = le32_to_cpu(bt_const.fclk_can);
	if (!dev->can.clock.freq)
		dev->can.clock.freq = VCAN_USB_CLOCK_HZ;
	dev->can.bittiming_const = &dev->bt_const;
	dev->can.do_set_bittiming = vcan_usb_set_bittiming;
	dev->can.do_set_mode = vcan_usb_set_mode;
	dev->can.do_get_berr_counter = vcan_usb_get_berr_counter;

	dev->can.ctrlmode_supported = 0;
	if (feature & VCAN_USB_FEATURE_LISTEN_ONLY)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_LISTENONLY;
	if (feature & VCAN_USB_FEATURE_LOOP_BACK)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_LOOPBACK;
	if (feature & VCAN_USB_FEATURE_TRIPLE_SAMPLE)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_3_SAMPLES;
	if (feature & VCAN_USB_FEATURE_ONE_SHOT)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_ONE_SHOT;
	if (feature & VCAN_USB_FEATURE_BERR_REPORTING)
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_BERR_REPORTING;

	if (feature & VCAN_USB_FEATURE_FD) {
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_FD;
#ifdef CAN_CTRLMODE_FD_NON_ISO
		dev->can.ctrlmode_supported |= CAN_CTRLMODE_FD_NON_ISO;
#endif
		dev->can.data_bittiming_const = &dev->bt_const;
		dev->can.do_set_data_bittiming = vcan_usb_set_data_bittiming;

		if (feature & VCAN_USB_FEATURE_BT_CONST_EXT) {
			struct vcan_usb_bt_const_ext ext;

			ret = vcan_usb_recv(dev, VCAN_USB_BREQ_BT_CONST_EXT,
					    &ext, sizeof(ext));
			if (ret) {
				dev_err(&intf->dev,
					"bt_const_ext read failed: %d\n", ret);
				goto err_free;
			}
			vcan_usb_load_bt_const(&dev->data_bt_const, &ext.dbtc);
			dev->can.data_bittiming_const = &dev->data_bt_const;
		}
	}

	if (!vcan_usb_get_termination(dev, &term))
		dev->termination = term;
	dev->can.termination_const = vcan_usb_termination_const;
	dev->can.termination_const_cnt = ARRAY_SIZE(vcan_usb_termination_const);
	dev->can.termination = dev->termination ? VCAN_USB_TERMINATION_ON :
						  VCAN_USB_TERMINATION_OFF;
	dev->can.do_set_termination = vcan_usb_set_termination;

	dev->rx_buf_sz = ALIGN(VCAN_USB_FRAME_DATA_OFFSET + 64,
			       usb_maxpacket(udev, dev->pipe_in));

	netdev->min_mtu = CAN_MTU;
	netdev->max_mtu = (feature & VCAN_USB_FEATURE_FD) ? CANFD_MTU : CAN_MTU;

	ret = register_candev(netdev);
	if (ret) {
		dev_err(&intf->dev, "register_candev failed: %d\n", ret);
		goto err_free;
	}

	if (have_info)
		vcan_usb_log_device_info(dev, &info);
	dev_info(&intf->dev, "VCAN channel %u registered as %s\n",
		 dev->channel, netdev->name);
	return 0;

err_free:
	usb_set_intfdata(intf, NULL);
	free_candev(netdev);
	return ret;
}

static void vcan_usb_disconnect(struct usb_interface *intf)
{
	struct vcan_usb *dev = usb_get_intfdata(intf);

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

static const struct usb_device_id vcan_usb_table[] = {
	{ USB_DEVICE(VCAN_USB_VID, VCAN_USB_PID) },
	{}
};
MODULE_DEVICE_TABLE(usb, vcan_usb_table);

static struct usb_driver vcan_usb_driver = {
	.name = VCAN_USB_DRIVER_NAME,
	.probe = vcan_usb_probe,
	.disconnect = vcan_usb_disconnect,
	.id_table = vcan_usb_table,
};

module_usb_driver(vcan_usb_driver);

MODULE_AUTHOR("VEK");
MODULE_DESCRIPTION("VCAN USB CAN-FD driver");
MODULE_VERSION(VCAN_USB_DRV_VERSION);
MODULE_LICENSE("GPL");
