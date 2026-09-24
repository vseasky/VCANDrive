# Linux SocketCAN API 开发参考

用户路线：先按[快速入门](../../udocs/快速入门.md)安装驱动并收发第一帧，再按[验收测试路线](../../udocs/验证路线.md)验收 Linux 内核路径，最后使用本页 API。

SocketCAN 把 CAN 控制器注册成 Linux 网络接口。应用只依赖 `PF_CAN`/`canX`，无需知道 VCANDrive 的 USB 端点或 vendor request。

## 最小且可复用的打开函数

```c
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <linux/can.h>
#include <linux/can/raw.h>
#include <net/if.h>

static int open_can_raw(const char *ifname, bool enable_fd)
{
    int fd = socket(PF_CAN, SOCK_RAW | SOCK_CLOEXEC, CAN_RAW);
    if (fd < 0)
        return -1;

    if (enable_fd) {
        int on = 1;
        if (setsockopt(fd, SOL_CAN_RAW, CAN_RAW_FD_FRAMES,
                       &on, sizeof(on)) < 0) {
            close(fd);
            return -1;
        }
    }

    unsigned int ifindex = if_nametoindex(ifname);
    if (ifindex == 0) {
        close(fd);
        errno = ENODEV;
        return -1;
    }

    struct sockaddr_can addr = {
        .can_family = AF_CAN,
        .can_ifindex = (int)ifindex,
    };
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}
```

`SOCK_CLOEXEC` 防止 fd 意外继承给子进程。波特率、FD、controller mode 与 termination 必须在打开 socket 前由 `ip link` 或 netlink 管理层配置。

## 发送经典 CAN

```c
struct can_frame frame = {
    .can_id = 0x123,
    .len = 4,
    .data = {0x11, 0x22, 0x33, 0x44},
};

ssize_t n = write(fd, &frame, CAN_MTU);
if (n != CAN_MTU) {
    /* n < 0: errno; unexpected short write: treat as failure */
}
```

扩展帧使用 `CAN_EFF_FLAG | (id & CAN_EFF_MASK)`，标准帧使用 `id & CAN_SFF_MASK`。RTR 加 `CAN_RTR_FLAG`；`CAN_ERR_FLAG` 只用于内核错误消息，不应由普通业务帧设置。

## 发送与接收 CAN FD

```c
struct canfd_frame frame = {
    .can_id = 0x321,
    .len = 16,
    .flags = CANFD_BRS,
    .data = {
        0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
        0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f,
    },
};

if (write(fd, &frame, CANFD_MTU) != CANFD_MTU) {
    /* handle error */
}
```

接收缓冲区可统一使用较大的 `canfd_frame`，但必须按返回字节数判断：

```c
struct canfd_frame frame;
ssize_t n = read(fd, &frame, sizeof(frame));

if (n == CAN_MTU) {
    struct can_frame *classic = (struct can_frame *)&frame;
    /* classic->len and classic->data are valid; frame.flags is not */
} else if (n == CANFD_MTU) {
    /* frame.flags, frame.len and up to 64 bytes are valid */
} else if (n < 0) {
    /* retry EINTR; handle EAGAIN, ENETDOWN, ENODEV, ... */
} else {
    /* reject unexpected frame size */
}
```

启用 `CAN_RAW_FD_FRAMES` 后，同一 socket 可能收到 classic 和 FD 两种 MTU。FD payload 长度是字节数，不是原始 4-bit DLC。

## poll/epoll 主循环

不要依赖永久阻塞 `read()` 完成整个应用状态机：

```c
#include <poll.h>

struct pollfd pfd = { .fd = fd, .events = POLLIN };
int rc;
do {
    rc = poll(&pfd, 1, 1000);
} while (rc < 0 && errno == EINTR);

if (rc > 0 && (pfd.revents & POLLIN)) {
    /* read one or drain multiple frames */
} else if (rc == 0) {
    /* application timeout, not automatically a CAN failure */
} else if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
    /* inspect interface and socket state */
}
```

高吞吐应用可使用 `epoll`，边沿触发时把 socket 设为 nonblocking 并读到 `EAGAIN`。把接收时间戳、队列溢出与业务处理分开，避免慢回调堵塞 socket 读取。

## 接收过滤

```c
struct can_filter filters[] = {
    { .can_id = 0x123, .can_mask = CAN_SFF_MASK },
    { .can_id = 0x180, .can_mask = 0x780 },
};

if (setsockopt(fd, SOL_CAN_RAW, CAN_RAW_FILTER,
               filters, sizeof(filters)) < 0) {
    /* handle error */
}
```

匹配规则是 `(received_id & mask) == (filter_id & mask)`，多个过滤器默认逻辑 OR。标准与扩展帧必须在 mask 中考虑 `CAN_EFF_FLAG`，否则可能发生意外匹配。传空 filter 列表可禁止数据帧接收。

过滤器属于当前 socket，是多进程可共存的关键；不要把它误称为 VCANDrive 固件硬件 filter。

## 错误帧与总线状态

```c
#include <linux/can/error.h>

can_err_mask_t error_mask = CAN_ERR_MASK;
if (setsockopt(fd, SOL_CAN_RAW, CAN_RAW_ERR_FILTER,
               &error_mask, sizeof(error_mask)) < 0) {
    /* handle error */
}
```

收到 `CAN_ERR_FLAG` 后依据 `<linux/can/error.h>` 解码；不要按 DBC 或普通业务 payload 解释。应用还应从 netlink/监控进程获取 ERROR-ACTIVE、ERROR-PASSIVE、BUS-OFF 与 berr-counter，避免仅凭无数据超时推断链路状态。

## 本地回显与自收消息

SocketCAN 默认启用本地 loopback，但发送 socket 默认不接收自己的帧：

```c
int on = 1;
setsockopt(fd, SOL_CAN_RAW, CAN_RAW_RECV_OWN_MSGS, &on, sizeof(on));
```

这只是 socket 的 TX echo 策略。`ip link ... loopback on` 会让 CAN 控制器进入内部回环，两者的测试目标和预期帧数不同。

## 绑定所有接口

把 `sockaddr_can.can_ifindex` 设为 0 可接收所有已启用 CAN 接口。此时用 `recvfrom()` 获取来源 ifindex，发送则用 `sendto()` 明确目标。只有确实需要聚合多总线时使用，避免业务数据误跨网络。

## 时间戳

需要内核接收时间时优先使用 `recvmsg()` 配合 `SO_TIMESTAMPNS`/`SO_TIMESTAMPING` 控制消息。明确区分：

- 主机内核收到帧的时间；
- 控制器硬件时间戳；
- 业务 payload 内的设备时间。

不要把三者混为同一个时钟域。当前 VCANDrive 文档中的固件时间戳支持需按具体后端实现核对。

## 选择 RAW、BCM 或 ISO-TP

- 单帧、应用自管周期与自定义协议：`CAN_RAW`。
- 内核管理周期发送、超时与内容变化通知：`CAN_BCM`。
- ISO 15765-2 分段传输：`CAN_ISOTP`，不要手写分段逻辑后仍称为 raw-frame API。
- CANopen：使用成熟 CANopen 用户态栈，通过 SocketCAN 收发 raw frames。

DBC 只负责 payload 与信号之间的映射，不改变 socket 协议选择。

## 无硬件测试

```bash
sudo modprobe vcan
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up
candump vcan0
cansend vcan0 123#11223344
```

`vcan0` 可验证 socket、过滤、DBC 与 CANopen 路由逻辑，但不能验证位时序、终端、ACK、BUS-OFF 或 VCANDrive USB 行为。

上游参考：[Linux Kernel SocketCAN 文档](https://docs.kernel.org/networking/can.html)。
