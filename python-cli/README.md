# Python CAN USB 后端

首次使用请先按[快速入门](../udocs/快速入门.md)完成模式识别、驱动安装与第一帧收发。

本目录包含两个独立安装、API 一致的 `python-can` 后端：

| 目录 | 固件模式 | `can.Bus` 接口名 |
|---|---|---|
| `vcan_usb/` | VCAN，USB ID `1d50:6080` | `vcan_usb` |
| `vkgs_usb/` | VKGS/gs_usb，USB ID `1d50:606f` | `vkgs_usb` |

只安装与设备当前固件模式对应的包。

## Linux 快速开始

```bash
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install --upgrade pip setuptools wheel
.venv-linux/bin/python -m pip install -e ./vkgs_usb
sudo ./canctl --interface vkgs_usb list
```

VCAN 将 `vkgs_usb` 改为 `vcan_usb`。

## Windows 快速开始

先为设备的每个 CAN `MI_xx` interface 绑定 WinUSB，然后执行：

```powershell
py --list
py -3 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-win\Scripts\python.exe -m pip install -e .\vkgs_usb
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb list
```

不要指定 `py -3.10`，除非 `py --list` 确认本机已安装该版本。

## 常用命令

Linux：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 send 0x123 11223344
sudo ./canctl --interface vkgs_usb --channel 1 recv --count 10
sudo ./can-test --interface vkgs_usb --channels 2 --mode bus --profile functional --frames 60
```

Windows：

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb --channel 0 send 0x123 11223344
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 2 --mode bus --profile functional --frames 60
```

Windows 上每个 `Bus` 独立打开目标 `MI_xx` WinUSB interface。两个终端可分别操作同一
物理设备的 channel 0 和 channel 1；同一个 channel 仍只能由一个进程占用。

`canctl` 和 `can-test` 是 Linux Bash 包装入口，不是 pip 生成的 console script。
Windows 直接调用对应 Python 文件。

完整文档：

- [PythonCAN 使用手册](../udocs/PythonCAN使用手册.md)
- [Python 终端工具](../udocs/Python终端工具.md)
- [Python API 参考](../udocs/PythonAPI参考.md)
- [独立 Windows 设备管理器](../udocs/设备管理器.md)

使用说明和公开 API 统一从 [文档导航](../udocs/README.md) 查阅。

Python 窗口压力、重复打开与运行参数见[测试说明](tests/README.md)。
