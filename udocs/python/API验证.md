# Python API 验证：两路收发一帧

这一步适合第一次写 `python-can` 程序的人。先按[快速入门](../start/快速入门.md)安装与设备模式匹配的后端，并用 `canctl list` 确认有两个通道。将 CANH 接 CANH、CANL 接 CANL，两端正确终端，且不要有其他程序占用这两个 USB interface。

下面的示例使用真正的 `can.Bus()`、`send()` 和 `recv()`，双向核对 ID、帧类型、BRS 和载荷。它只验证最小 API 路径；丢包、背压、位率矩阵和长时间稳定性继续按[完整验收](../verify/验证路线.md)测试。

## 1. Windows 运行

在仓库的 `python-cli/` 目录打开 PowerShell。根据当前 USB 模式执行其中一条；`channel` 编号要以 `list` 输出为准。

```powershell
$py = '.\.venv-win\Scripts\python.exe'
& $py .\tools\canctl.py --interface vcan_usb list
& $py .\examples\api_check.py --interface vcan_usb
```

GS_CAN 模式把两处 `vcan_usb` 改为 `vkgs_usb`。多台设备同时接入时，先从 `list` 取得目标 `port_path`，再加 `--port-path 2.1.4`；两个通道必须属于同一台设备。

## 2. Linux 运行

在 `python-cli/` 目录、已安装对应 Python 后端且释放内核驱动后执行：

```bash
sudo ./canctl --interface vcan_usb list
sudo .venv-linux/bin/python examples/api_check.py --interface vcan_usb
```

GS_CAN 模式同样改为 `vkgs_usb`。Linux 内核驱动若仍占用设备接口，先按[Python CAN 手册](PythonCAN使用手册.md)释放，再运行直接 USB 示例。使用 SocketCAN 的 C/C++ 项目可直接看[SocketCAN API 参考](../../socket-can-skill/references/socketcan-api.md)。

macOS 完成[Python 环境安装](PythonCAN使用手册.md#13-macos)后，在 `python-cli/` 目录执行 `.venv-mac/bin/python examples/api_check.py --interface vcan_usb`；GS_CAN 同样替换接口名。

## 3. 看结果并继续

成功时应看到两行 `PASS`，分别对应 `0x321` 和 `0x322` 两个方向，最后输出 `COMPLETE API CHECK PASSED`。每行的 TX/RX 帧数相等，且 ID、FD/BRS 标志与载荷一致。接收超时或内容不同会打印 `FAIL` 并返回非零退出码；这时先核对两路位率、CANH/CANL、两端终端电阻和是否有其他程序占用通道。

先用经典 CAN 验证。两路都支持 FD 时，再单独执行 CAN FD+BRS：

```powershell
& $py .\examples\api_check.py --interface vcan_usb --fd
```

脚本默认每个方向 1 帧，也可加 `--frames 100` 做少量 API 回归；高负载验收请使用 `tests/hardware_test.py`，其默认每个发送端、每个阶段 10000 帧。API 参数、设备发现、终端电阻和生命周期见[Python API 参考](PythonAPI参考.md)。
