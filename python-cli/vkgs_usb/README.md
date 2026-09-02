# vkgs-usb

这是 VKGS/gs_usb 扩展固件（USB `1d50:606f`）对应的 `python-can` 后端包，
仅注册 `interface="vkgs_usb"`。

## 快速安装

```bash
python3 -m pip install -e ./vkgs_usb
```

Linux 开发安装：

```bash
.venv-linux/bin/python -m pip install -e ./vkgs_usb
```

Windows：

```powershell
.\.venv-win\Scripts\python.exe -m pip install -e .\vkgs_usb
```

## 使用示例

```python
import can

with can.Bus(interface="vkgs_usb", channel=0, bitrate=1_000_000) as bus:
    bus.send(can.Message(
        arbitration_id=0x123,
        is_extended_id=False,
        data=bytes.fromhex("01 02 03 04"),
    ))
```

设备定位参数：`index`、`bus`、`address`、`port_path`；其中 `channel` 为 USB interface 序号（与 Linux 的 `can0/can1` 映射关系需按实际设备判断）。
Windows 使用前需为目标 `MI_xx` 绑定 WinUSB。

## 包内容（职责边界）

```text
vkgs_usb/
|-- __init__.py   公共导出
|-- bus.py        python-can 适配、设备选择、生命周期
|-- protocol.py   VKGS/gs_usb 控制请求与帧编码解码
|-- transport.py   Linux/macOS 异步 libusb 传输
`-- winusb.py     Windows 独立 MI_xx 传输与 EP0 管理
```

本包不包含 vcan 协议、终端脚本或硬件测试逻辑。
完整使用与验收流程见：[`docs/PythonCAN使用手册.md`](../../docs/PythonCAN使用手册.md)。




