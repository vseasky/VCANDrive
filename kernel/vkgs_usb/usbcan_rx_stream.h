/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _USBCAN_RX_STREAM_H
#define _USBCAN_RX_STREAM_H

/* REF firmware FIFO bound; independent of the USB endpoint packet size. */
#define USBCAN_RX_RECORD_MAX 512U
#define USBCAN_RX_MARKER_SIZE 4U

struct usbcan_rx_stream {
	u8 data[USBCAN_RX_RECORD_MAX];
	unsigned int used;
	unsigned int needed;
};

/* Feed exactly one USB packet. Full packets need no ZLP to complete the URB.
 * A zero marker ends this packet's bundle padding, not the next USB packet.
 * Records spanning packets are retained; allocation is bounded and reusable.
 * Return false for malformed/truncated records so the caller can count errors.
 */
static inline bool usbcan_rx_feed(struct usbcan_rx_stream *s,
		const u8 *data, unsigned int len, unsigned int header_size,
		int (*record_size)(const u8 *, int),
		void (*record)(void *, const u8 *, int), void *context)
{
	unsigned int take, target;
	int size;

	if (!len) {
		if (s->used)
			goto invalid;
		return true;
	}
	while (len) {
		target = s->used < USBCAN_RX_MARKER_SIZE ? USBCAN_RX_MARKER_SIZE :
			 s->needed ? s->needed : header_size;
		take = min(len, target - s->used);
		memcpy(s->data + s->used, data, take);
		s->used += take;
		data += take;
		len -= take;
		if (s->used < target)
			continue;
		if (!s->data[0] && !s->data[1] && !s->data[2] && !s->data[3]) {
			s->used = s->needed = 0;
			return true;
		}
		if (!s->needed && s->used == header_size) {
			size = record_size(s->data, header_size);
			if (size < (int)header_size || size > (int)USBCAN_RX_RECORD_MAX)
				goto invalid;
			s->needed = size;
		}
		if (s->needed && s->used == s->needed) {
			record(context, s->data, s->needed);
			s->used = s->needed = 0;
		}
	}
	return true;
invalid:
	s->used = s->needed = 0;
	return false;
}
#endif
