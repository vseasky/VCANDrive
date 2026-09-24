# vcan-usb

首次使用请先看[快速入门](../../udocs/快速入门.md)，确认设备模式、安装对应包并验收收发。

VCAN 固件（USB ID `1d50:6080`）的独立 `python-can` 后端，只注册
`interface="vcan_usb"`。

以下安装命令从仓库的 `python-cli/` 目录执行，先按[快速入门](../../udocs/快速入门.md)建立虚拟环境。

```bash
python3 -m pip install ./vcan_usb
```

仓库内开发安装：

```bash
.venv-linux/bin/python -m pip install -e ./vcan_usb
```

```powershell
.\.venv-win\Scripts\python.exe -m pip install -e .\vcan_usb
```

发送前先让另一通道或外部节点以相同位率在线并配置正确终端；发送无异常不代表对端已经收到，可按[验收测试路线](../../udocs/验证路线.md)核对收发一致。

```python
import can

with can.Bus(interface="vcan_usb", channel=0, bitrate=1_000_000) as bus:
    bus.send(can.Message(
        arbitration_id=0x123,
        is_extended_id=False,
        data=[1, 2, 3, 4],
    ))
```

设备选择参数包括 `index`、`bus`、`address` 和 `port_path`。Windows 使用前需要为每个
目标 USB `MI_xx` interface 绑定 WinUSB。

该发行包不包含 VKGS 协议代码、终端工具或测试。包内职责：

```text
vcan_usb/
|-- __init__.py   公共导出
|-- bus.py        python-can 适配、设备选择和生命周期
|-- protocol.py   VCAN 控制请求及帧编解码
|-- transport.py  libusb 发现及 Linux/macOS 异步传输
`-- winusb.py     Windows 按 MI_xx 独立传输
```

完整安装、并发模型和验收流程见仓库
[PythonCAN 使用手册](../../udocs/PythonCAN使用手册.md)。
