/* Userspace harness for the actual driver RX handler, inserted by the runner. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <endian.h>
#include <linux/can.h>

typedef uint32_t u32;
typedef uint8_t u8;
#define BIT(n) (1U << (n))
#define le32_to_cpu(x) le32toh(x)
#define __packed __attribute__((packed))

struct net_device {
	struct { unsigned int rx_frame_errors, rx_dropped; } stats;
};
struct vkgs_usb { struct net_device *netdev; };
struct sk_buff { int unused; };
static struct sk_buff skb;
static struct canfd_frame received;
static unsigned int delivered, bytes;
static struct sk_buff *alloc_canfd_skb(struct net_device *n,
				      struct canfd_frame **f)
{
	(void)n;
	memset(&received, 0, sizeof(received));
	*f = &received;
	return &skb;
}
static struct sk_buff *alloc_can_skb(struct net_device *n, struct can_frame **f)
{
	struct canfd_frame *fd;
	alloc_canfd_skb(n, &fd);
	*f = (struct can_frame *)fd;
	return &skb;
}
static unsigned int usbcan_fd_dlc2len(unsigned int dlc)
{
	static const u8 lengths[] = {0,1,2,3,4,5,6,7,8,12,16,20,24,32,48,64};
	return lengths[dlc];
}
static unsigned int usbcan_cc_dlc2len(unsigned int dlc)
{
	return dlc > 8 ? 8 : dlc;
}
static void vkgs_usb_account_rx(struct vkgs_usb *d, unsigned int len, bool over)
{
	(void)d;
	(void)over;
	bytes += len;
}
static void netif_rx(struct sk_buff *s) { (void)s; delivered++; }

/* INSERT_DRIVER */

int main(void)
{
	struct net_device n = {0};
	struct vkgs_usb d = {.netdev = &n};
	unsigned char storage[sizeof(struct vkgs_usb_host_frame) + 64] = {0};
	struct vkgs_usb_host_frame *f = (void *)storage;
	unsigned int extended, esi, brs, dlc, i, cases = 0;

	/* Both ID formats, both BRS/ESI states and every legal FD DLC.
	 * Reproduce firmware duplication of ESI into CAN-ID bit 29.
	 */
	for (extended = 0; extended < 2; extended++)
	for (esi = 0; esi < 2; esi++)
	for (brs = 0; brs < 2; brs++)
	for (dlc = 0; dlc < 16; dlc++) {
		u32 id = extended ? CAN_EFF_FLAG | 0x18c15959 : 0x415;
		unsigned int len = usbcan_fd_dlc2len(dlc);
		f->can_id = htole32(id | (esi ? CAN_ERR_FLAG : 0));
		f->flags = VKGS_USB_FLAG_FD |
			(esi ? VKGS_USB_FLAG_ESI : 0) |
			(brs ? VKGS_USB_FLAG_BRS : 0);
		f->can_dlc = dlc;
		for (i = 0; i < 64; i++)
			f->data[i] = i ^ 0xa5;
		delivered = bytes = 0;
		vkgs_usb_handle_rx_frame(&d, f);
		assert(delivered == 1 && bytes == len);
		assert(received.can_id == id);
		assert(received.flags == ((esi ? CANFD_ESI : 0) |
					 (brs ? CANFD_BRS : 0)));
		assert(received.len == len);
		assert(!memcmp(received.data, f->data, len));
		cases++;
	}
	/* Classical error frames must retain their error flag. */
	f->flags = 0;
	f->can_id = htole32(CAN_ERR_FLAG | 0x40);
	f->can_dlc = 8;
	vkgs_usb_handle_rx_frame(&d, f);
	assert(received.can_id == (CAN_ERR_FLAG | 0x40));
	printf("RX decode passed: %u FD cases and Classical error preservation\n", cases);
	return 0;
}
