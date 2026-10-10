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
	unsigned long long sequence, origin_sequence;
	unsigned int origin_offset;
};

/* Feed exactly one USB packet. Full packets need no ZLP to complete the URB.
 * A zero marker ends this packet's bundle padding, not the next USB packet.
 * Records spanning packets are retained; allocation is bounded and reusable.
 * Return false for malformed/truncated records so the caller can count errors.
 */
static inline bool usbcan_rx_feed(struct usbcan_rx_stream *s,
		const u8 *data, unsigned int len, unsigned int header_size, bool zero_terminator,
		int (*record_size)(void *, const u8 *, int),
		void (*record)(void *, const u8 *, int), void *context)
{
	unsigned int take, target, offset = 0;
	int size;

	s->sequence++;
	if (header_size < 4 || header_size > USBCAN_RX_RECORD_MAX)
		goto invalid;
	if (!len) {
		if (s->used)
			goto invalid;
		return true;
	}
	while (len) {
		if (!s->used) {
			s->origin_sequence = s->sequence;
			s->origin_offset = offset;
		}
		target = s->used < USBCAN_RX_MARKER_SIZE ? USBCAN_RX_MARKER_SIZE :
			 s->needed ? s->needed : header_size;
		take = min(len, target - s->used);
		memcpy(s->data + s->used, data, take);
		s->used += take;
		data += take;
		len -= take;
		offset += take;
		if (s->used < target)
			continue;
		if (zero_terminator && !s->data[0] && !s->data[1] && !s->data[2] && !s->data[3]) {
			s->used = s->needed = 0;
			return true;
		}
		if (!s->needed) {
			/* Validate the marker as soon as it is complete. A partial
			 * header returns zero; it is not yet a body-length decision.
			 */
			size = record_size(context, s->data, s->used);
			if (size < 0)
				goto invalid;
			if (s->used == header_size) {
				if (size < (int)header_size || size > (int)USBCAN_RX_RECORD_MAX)
					goto invalid;
				s->needed = size;
			}
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
