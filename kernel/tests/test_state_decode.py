#!/usr/bin/env python3
"""Compile the production state handlers against small userspace stubs."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for driver in ('vcan_usb', 'vkgs_usb'):
    source = (ROOT / driver / (driver + '.c')).read_text()
    start = source.index('static void ' + driver + '_handle_state(')
    handler = source[start:source.index('\n}', start) + 2]
    state_struct = 'device_state' if driver == 'vcan_usb' else 'state_ext'
    prefix = driver.upper()
    fixture = r'''
#include <assert.h>
#include <stdint.h>
#include <linux/can.h>
#include <linux/can/error.h>
typedef uint32_t u32;
#define le32_to_cpu(x) (x)
struct net_device { struct { unsigned rx_packets, rx_bytes; } stats; };
struct DRIVER { struct net_device *netdev; struct { unsigned rxerr, txerr; } bec; };
struct DRIVER_STATE { u32 state, rxerr, txerr; };
struct sk_buff { int unused; };
static struct sk_buff skb;
static struct can_frame frame;
static unsigned delivered;
static struct sk_buff *alloc_can_err_skb(struct net_device *n, struct can_frame **f)
{ (void)n; frame = (struct can_frame){.can_id=CAN_ERR_FLAG, .len=8}; *f=&frame; return &skb; }
static void netif_rx(struct sk_buff *s) { (void)s; delivered++; }
static void DRIVER_set_state(struct DRIVER *d, u32 state) { (void)d; (void)state; }
#define PREFIX_CAN_STATE_ERROR_ACTIVE 0
#define PREFIX_CAN_STATE_ERROR_WARNING 1
#define PREFIX_CAN_STATE_ERROR_PASSIVE 2
#define PREFIX_CAN_STATE_BUS_OFF 3
'''.replace('DRIVER_STATE', driver + '_' + state_struct).replace('DRIVER', driver).replace('PREFIX', prefix)
    compat = (ROOT / driver / "usbcan_compat.h").read_text()
    begin = compat.index("#ifndef CAN_ERR_CNT")
    fixture += compat[begin:compat.index("#endif", begin) + 6] + "\n"
    tests = r'''
int main(void) {
 struct net_device n = {0}; struct DRIVER d = {.netdev=&n};
 struct DRIVER_STATE s = {0};
 DRIVER_handle_state(&d, &s); assert(delivered == 0);
 s.state=1; s.rxerr=96; s.txerr=0;
 DRIVER_handle_state(&d, &s);
 assert(frame.data[1] == CAN_ERR_CRTL_RX_WARNING);
 assert(frame.can_id & CAN_ERR_CNT); assert(frame.data[7] == 96);
 s.state=2; s.rxerr=0; s.txerr=128;
 DRIVER_handle_state(&d, &s);
 assert(frame.data[1] == CAN_ERR_CRTL_TX_PASSIVE);
 assert(frame.data[6] == 128); assert(frame.data[7] == 0);
 s.rxerr=128; DRIVER_handle_state(&d, &s);
 assert(frame.data[1] == (CAN_ERR_CRTL_RX_PASSIVE | CAN_ERR_CRTL_TX_PASSIVE));
 s.state=3; DRIVER_handle_state(&d, &s); assert(frame.can_id & CAN_ERR_BUSOFF);
 return 0;
}
'''.replace('DRIVER_STATE', driver + '_' + state_struct).replace('DRIVER', driver)
    with tempfile.TemporaryDirectory() as tmp:
        binary = str(Path(tmp) / 'state_decode')
        subprocess.run([os.environ.get('CC', 'cc'), '-x', 'c', '-std=gnu11',
                        '-Wall', '-Wextra', '-Werror', '-o', binary, '-'],
                       input=fixture + handler + tests, text=True, check=True)
        subprocess.run([binary], check=True)
    print(driver + ': state decode passed')
