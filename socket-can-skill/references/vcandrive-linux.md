# VCANDrive Linux 使用参考

首次安装从[Linux 驱动安装](../../udocs/08_Linux内核驱动安装.md)开始；下面的命令在仓库根目录执行。示例以 VCAN 模式为主，GS_CAN 用户将 `vcan_usb` 改为 `vkgs_usb`。

## 确认设备与驱动

| 模式 | USB ID | 模块 |
|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` |
| GS_CAN | `1d50:606f` | `vkgs_usb` |

```bash
lsusb
ip -br link show type can
```

如果没有出现两路 `canX`，先按[驱动安装](../../udocs/08_Linux内核驱动安装.md)检查 USB 模式、编译依赖和实际绑定的模块。GS_CAN 可能先被系统 `gs_usb` 绑定。`can0/can1` 是动态接口名，不能仅凭数字判断板内通道。

测试脚本可编译、加载并检查绑定；仅做加载检查时运行：

```bash
sudo bash kernel/tests/vcan_usb/test_vcan_usb.sh --load-only
```

临时加载的模块若未安装到系统模块目录，卸载时用 `sudo rmmod vcan_usb`；`modprobe -r` 提示找不到模块不代表它没有加载。GS_CAN 对应 `vkgs_usb`。需要切换至 Python USB 时，先关闭使用接口的程序，再按[常见问题](../../udocs/02_常见问题.md)释放目标板卡的内核绑定。

## 定位唯一板卡和通道

按[身份查询](../../udocs/09_SocketCAN使用手册.md#定位唯一板卡和通道)读取每个 `canX` 对应的 UUID 与板内通道号。完整 UUID 定位板卡，`UUID:通道号` 定位唯一通道。插拔或重启后重新核对 `canX` 映射。

## 配置与收发

先确认两路 CANH/CANL 接线、位率一致，且总线两端各有一个 120 Ω 终端。已有外置终端时不要重复开启内置终端。在接口关闭时配置：

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000 restart-ms 100
sudo ip link set can0 up
ip -details link show can0
```

另一端若为 `can1`，以相同位率启用。先在一个终端接收，再在另一个终端发送：

```bash
candump -ta -e can1
```

```bash
cansend can0 123#11223344
```

接收端应看到 ID `123` 与数据 `11 22 33 44`。如果超时或错误计数增长，先查[常见问题](../../udocs/02_常见问题.md)中的接线、终端、ACK 和位率问题。

CAN FD 的位率和发送格式见[SocketCAN 手册](../../udocs/09_SocketCAN使用手册.md)。状态查看命令：

```bash
ip -details -statistics link show can0
candump -e can0
```

## 完整硬件验收

第一帧成功后，在隔离测试总线上运行：

```bash
sudo bash kernel/tests/vcan_usb/test_vcan_usb.sh --frames 2000
```

这条命令会编译或加载所需驱动、重配接口并发送真实流量。`--no-build` 会同时跳过编译和加载，只适用于两路接口已经绑定到当前驱动的情况；若提示找到 0 路，先运行上面的 `--load-only`，再检查绑定。GS_CAN 使用 `kernel/tests/vkgs_usb/test_vkgs_usb.sh`。成功标志、测试范围和保存结果的方法见[验收路线](../../udocs/10_验证路线.md)。
