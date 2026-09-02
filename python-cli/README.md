# Python CAN USB 后端（vcan_usb / vkgs_usb）

本目录用于维护与 HPMicro USB-CAN 软件栈配套的 `python-can` 后端包。
`vcan_usb` 与 `vkgs_usb` 结构一致，但只包含各自协议实现，不会混装。

| 目录 | 固件模式 | 设备 VID:PID | `can.Bus` 接口名 |
|---|---|---|---|
| `vcan_usb/` | VCAN | `1d50:6080` | `vcan_usb` |
| `vkgs_usb/` | VKGS / gs_usb | `1d50:606f` | `vkgs_usb` |

> 只安装与当前固件模式一致的后端。

## 1. 快速开始

### Linux / macOS

```bash
cd /path/to/04.VCANDrive/python-cli

python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install --upgrade pip setuptools wheel
.venv-linux/bin/python -m pip install -e ./vkgs_usb

# VCAN 模式请把 vkgs_usb 改为 vcan_usb
sudo ./canctl --interface vkgs_usb list
```

### Windows

```powershell
cd C:\path\to\04.VCANDrive\python-cli

py --list
py -3 -m venv .venv-win
.\.venv-win\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv-win\Scripts\python.exe -m pip install -e .\vkgs_usb

# VCAN 模式请把 vkgs_usb 改为 vcan_usb
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb list
```

## 2. 该目录包含什么

- `vcan_usb/` 与 `vkgs_usb/`：发布/开发安装的 Python 包；
- `tools/`：命令行入口逻辑（`canctl.py`）；
- `tests/`：单元测试与硬件验收脚本（`hardware_test.py`）。

`canctl`/`can-test` 是仓库内的 bash/本地脚本，不属于 `pip install` 的 console entry。

## 3. 常用命令

Linux：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 send 0x123 11223344
sudo ./canctl --interface vkgs_usb --channel 1 recv --count 10
sudo ./can-test --interface vkgs_usb --channels 2 --mode bus --frames 60
```

Windows：

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb --channel 0 send 0x123 11223344
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 2 --mode bus --frames 60
```

## 4. 并发与通道模型（关键约定）

- `channel` 是 USB interface 索引，不是 Linux 的 `can0/can1`；
- 同一物理设备的两路默认 `channel=0/1`；
- 多个进程可分别独立打开同一物理设备不同 `channel`，但同一 `channel` 不能并发；
- Windows 下每个 `channel` 会映射为独立 WinUSB path（`MI_xx`）。

建议在多设备环境优先使用 `--usb-port-path`；`bus/address` 可作二级过滤。

## 5. 验证与测试

单元测试（不接硬件）：

```bash
cd python-cli
PYTHONDONTWRITEBYTECODE=1 .venv-linux/bin/python -m unittest discover -s tests/unit -v
```

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv-win\Scripts\python.exe -m unittest discover -s tests\unit -v
```

硬件验收脚本见 `tests/hardware_test.py`，与《[Python 终端工具](../docs/Python终端工具.md)》与
《[PythonCAN 使用手册](../docs/PythonCAN使用手册.md)》参数一致。

## 6. 进一步阅读

- [PythonCAN 使用手册](../docs/PythonCAN使用手册.md)
- [Python API 参考](../docs/PythonAPI参考.md)
- [Python 终端工具](../docs/Python终端工具.md)
- [驱动特殊 API 说明](../docs/驱动特殊API说明.md)
- [开发约束记录](PYTHON_CAN_DEVELOPMENT.md)




