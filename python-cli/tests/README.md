# Python USB 双通道完整验收

先按[快速入门](../../udocs/start/快速入门.md)完成模式识别、驱动安装和第一帧收发。把设备的 CANH 对 CANH、CANL 对 CANL 接在同一条隔离测试总线上。脚本会设置两个通道的终端电阻、位时序与工作状态；运行时不要让其他程序占用同一 USB 接口。

在 `python-cli/` 目录运行 Windows 命令：

```powershell
$py = '.\.venv-win\Scripts\python.exe'
& $py .\tests\hardware_test.py --interface vcan_usb
```

GS_CAN 模式改为 `--interface vkgs_usb`。Linux 中卸载对应内核驱动后可用 `sudo ./can-test --interface vcan_usb` 运行相同的 Python USB 测试。脚本只提供这一套完整测试：两路物理总线的混合经典/FD 帧、定向序号、多位率、双向数据完整性、每路内部回环、反复打开。两个通道必须支持 CAN FD；少于两路或 `--frames` 少于 100 都直接失败，不会给出“部分通过”。

默认每个发送端、每个阶段发送 10000 帧，窗口为 32，完整测试运行 1 轮。高负载长测可用：

```powershell
& $py .\tests\hardware_test.py --interface vcan_usb --frames 20000 --rounds 3 --results-json "$env:TEMP\vcandrive-usb-test.json"
```

`--frames` 作用于每个发送端的每个阶段，不是整个测试的帧数。增加 `--window` 会增加尚未确认的批量发送量，可能更快暴露背压或缓冲问题；排查失败时先保留默认 32。多设备时用 `--device` 或 `--usb-port-path` 指定设备，`--reopen-loops` 调整每轮重复打开次数。查看完整选项：`& $py .\tests\hardware_test.py --help`。

每阶段应显示两方向帧数与载荷字节数一致、异常/重复/重排为 0、USB 错误与应用缓冲丢弃为 0，控制器保持 ACTIVE。只有所有阶段完成才输出 `COMPLETE PYTHON USB SUITE PASSED`。JSON 中的 `all_completed_phases_passed` 只有整套完成才为 true。Linux 内核的 ISO-TP、J1939 TP、ECU 周期调度和实测总线负载另用 [SocketCAN 完整验收](../../udocs/verify/验证路线.md#linux-socketcan-完整验收)；Python USB 测试不会冒充这些内核专项。
