# Python USB 双通道完整验收

先按[快速入门](../../udocs/01_快速入门.md)完成模式识别、驱动安装和第一帧收发。把设备的 CANH 对 CANH、CANL 对 CANL 接在同一条隔离测试总线上。脚本会设置两个通道的终端电阻、位时序与工作状态；运行时不要让其他程序占用同一 USB 接口。

在 `python-cli/` 运行同一入口。Windows：

```powershell
py canctl.py test --interface vkgs_usb --frames 2000
```

Linux/macOS：

```bash
python3 canctl.py test --interface vkgs_usb --frames 2000
```

VCAN 模式改为 `--interface vcan_usb`。Linux 先按快速入门配置 udev 权限、用 doctor 检查并释放内核绑定，测试本身不用 sudo。脚本会设置两路内置终端，测试总线不要重复安装外部终端。它执行混合经典/FD 帧、定向序号、多位率、双向数据完整性、内部回环和反复打开。两个通道必须支持 FD；少于两路或 `--frames` 少于 100 都直接失败。

默认每个发送端、每个阶段发送 10000 帧，窗口为 32，完整测试运行 1 轮。高负载长测可用：

```powershell
py canctl.py test --interface vcan_usb --frames 20000 --rounds 3 --results-json results.json
```

`--frames` 作用于每个发送端的每个阶段，不是整个测试的帧数。增加 `--window` 会增加尚未确认的批量发送量，可能更快暴露背压或缓冲问题；排查失败时先保留默认 32。多设备时用 `--device` 或 `--usb-port-path` 指定设备，`--reopen-loops` 调整每轮重复打开次数。查看完整选项：`py canctl.py test --help`。

每阶段应显示两方向帧数与载荷字节数一致、异常/重复/重排为 0、USB 错误与应用缓冲丢弃为 0，控制器保持 ACTIVE。只有所有阶段完成才输出 `COMPLETE PYTHON USB SUITE PASSED`。JSON 中的 `all_completed_phases_passed` 只有整套完成才为 true。Linux 内核的 ISO-TP、J1939 TP、ECU 周期调度和实测总线负载另用 [SocketCAN 完整验收](../../udocs/10_验证路线.md#linux-socketcan-完整验收)；Python USB 测试不会冒充这些内核专项。
