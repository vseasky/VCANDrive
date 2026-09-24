# Python 终端工具（canctl / can-test）

新用户先按[快速入门](快速入门.md)完成安装和一帧收发，再使用本页的完整命令。仓库提供两个命令入口：

| 工具 | 文件 | 角色 |
|---|---|---|
| `canctl` | `tools/canctl.py` | 日常收发、发现、终端电阻、USB 统计、协议切换 |
| `can-test` | `tests/hardware_test.py` | 双通道硬件验收（总线互通/内部回环） |

两个工具都使用公开的 `python-can` 后端（`vcan_usb` 或 `vkgs_usb`），不属于 `python-can` 的可安装 `console_script`。
运行示例默认在 `VCANDrive/python-cli/` 目录。

## 1. 运行入口

### 1.1 Linux / macOS

`./canctl` 与 `./can-test` 为 Bash 包装脚本：

```bash
./canctl    -> .venv-linux/bin/python tools/canctl.py
./can-test  -> .venv-linux/bin/python tests/hardware_test.py
```

脚本是可执行文件，如权限缺失执行：

```bash
chmod +x canctl can-test
```

### 1.2 Windows

Windows 下直接调用解释器：

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --help
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --help
```

无 `sudo`/管理员要求，但需要正确绑定 WinUSB。

## 2. 参数模型（先学会这一层）

`canctl` 支持两个层级：

- 全局参数（`--interface`、`--channel`、`--bitrate`、`--fd`…）
- 子命令（`list`、`send`、`recv`、`termination`、`stats`、`state`、`usb_mode`）

`list` 以外的子命令通常都要写 `--channel`，因为同一设备通常会有 ch0/ch1。

常用全局参数：

| 参数 | 作用 |
|---|---|
| `--interface` | 后端名称：`vcan_usb` 或 `vkgs_usb` |
| `--channel` | USB interface 号（不是 Linux 的 `can0/can1`） |
| `--device` | 同 VID/PID 设备中的序号 |
| `--usb-bus` / `--usb-address` | 总线/地址（二级选择） |
| `--usb-port-path` | USB 拓扑路径（推荐用于多设备） |
| `--bitrate` | 仲裁位率 |
| `--fd` / `--data-bitrate` | 开启 FD 与数据位率 |
| `--termination` | 建议的 `keep/on/off` |
| `--loopback` | 启动控制器内部回环 |

建议所有长期稳定脚本优先加 `--usb-port-path`，避免拔插/重插后 `--device` 偏移。

## 3. 设备发现与定位

Linux：

```bash
sudo ./canctl --interface vkgs_usb list
```

Windows：

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb list
```

输出示例：

```text
channel=0 bus=2 address=10 port_path=2.1.4 ep_in=0x81 ep_out=0x01 in_mps=512 out_mps=512
channel=1 bus=2 address=10 port_path=2.1.4 ep_in=0x82 ep_out=0x02 in_mps=512 out_mps=512
```

同一组 `bus/address/port_path` 通常是同一物理设备两路 CAN；`channel` 是接口索引。

## 4. 发送（send）

经典 CAN：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 --bitrate 1000000 send 0x123 1122334455667788
```

扩展帧、循环发送：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 send 0x18DAF110 01020304 --extended
sudo ./canctl --interface vkgs_usb --channel 0 send 0x123 A55A --count 100 --gap 0.01
```

RTR：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 send 0x123 --rtr --dlc 8
```

CAN FD：

```bash
sudo ./canctl --interface vkgs_usb --channel 0 --fd --bitrate 1000000 --data-bitrate 5000000 send 0x123 000102030405060708090A0B0C0D0E0F
sudo ./canctl --interface vkgs_usb --channel 0 --fd --bitrate 1000000 --data-bitrate 5000000 send 0x123 000102030405060708090A0B0C0D0E0F --brs
```

Windows 例子仅将命令行前缀换为 python 调用。

`send` 完整度约束：经典帧最大 8 字节，FD 最大 64 字节。

## 5. 接收（recv）

```bash
sudo ./canctl --interface vkgs_usb --channel 1 recv
```

```bash
sudo ./canctl --interface vkgs_usb --channel 1 recv --count 100 --timeout 2
```

持续监听：

```bash
sudo ./canctl --interface vkgs_usb --channel 1 recv --count 0 --timeout 1
```

## 6. 双终端并发表现验证

### 6.1 Linux/macOS

```bash
sudo ./canctl --interface vkgs_usb --channel 1 recv --count 60 --timeout 5
```

```bash
sudo ./canctl --interface vkgs_usb --channel 0 send 0x123 A55A --count 60 --gap 0.02
```

### 6.2 Windows

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb --channel 1 --termination on recv --count 60 --timeout 5
```

```powershell
.\.venv-win\Scripts\python.exe .\tools\canctl.py --interface vkgs_usb --channel 0 --termination on send 0x123 A55A --count 60 --gap 0.02
```

要点：

- 不要同时打开同一个 `channel`；
- 每个 `channel` 建议先后独立释放；
- 多设备时配合 `--usb-port-path` 指定目标。

## 7. 内部回环与硬件验收（can-test）

### 7.1 单通道内部回环

```bash
sudo ./can-test --interface vkgs_usb --channels 1 --mode loopback --frames 60
```

```powershell
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 1 --mode loopback --frames 60
```

### 7.2 双通道总线互通

```bash
sudo ./can-test --interface vkgs_usb --channels 2 --mode bus --frames 60
```

```powershell
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 2 --mode bus --frames 60
```

脚本会按顺序验证：

1. 共享总线经典 CAN
2. 共享总线 FD（BRS off）
3. 共享总线 FD（BRS on）
4. 独立内部回环经典
5. 独立内部回环 FD（BRS off）
6. 独立内部回环 FD（BRS on）

每个阶段都要求接收数等于发送数。

## 8. 设备状态与统计

### 8.1 终端电阻

```bash
sudo ./canctl --interface vkgs_usb --channel 0 termination show
sudo ./canctl --interface vkgs_usb --channel 0 termination on
sudo ./canctl --interface vkgs_usb --channel 0 termination off
```

### 8.2 USB 统计与总线状态

```bash
sudo ./canctl --interface vkgs_usb --channel 0 stats --duration 2
sudo ./canctl --interface vkgs_usb --channel 0 state --duration 2
```

`stats`/`state` 为当前 interface 的 USB 与总线事件采样，含 `rxerr`、`txerr`、`state` 等。

### 8.3 识别灯（仅 vkgs_usb）

```bash
sudo ./canctl --interface vkgs_usb --channel 0 identify on
sudo ./canctl --interface vkgs_usb --channel 0 identify off
```

`vcan_usb` 后端不支持此命令，执行时会给出明确报错。

## 9. USB 协议模式切换（usb_mode）

该命令会写 Flash 并重启：

```bash
# VKGS/gs_usb -> VCAN
sudo ./canctl --interface vkgs_usb --channel 0 usb_mode vcan

# VCAN -> VKGS/gs_usb
sudo ./canctl --interface vcan_usb --channel 0 usb_mode gs_usb

# VCAN -> PEAK 兼容
sudo ./canctl --interface vcan_usb --channel 0 usb_mode peak
```

切换前请先关闭该物理设备上的所有 `Bus` 与 SocketCAN 通道。

## 10. 常见错误速查

| 现象 | 含义 | 建议 |
|---|---|---|
| `Permission denied` | Linux 权限不足 | 先处理 udev 规则或用 `sudo` |
| `virtual environment not found` | 当前目录没按文档建 `.venv-linux/.venv-win` | 按 `PythonCAN 使用手册` 重建 |
| `.venv-win\\Scripts\\python.exe` 不存在 | Windows 环境异常 | 重新创建虚拟环境 |
| `No module named vcan_usb/vkgs_usb` | 依赖未安装到当前解释器 | 对当前 venv 执行 `pip install -e` |
| `USB device index 0 not found` | 设备定位失败 | 核对 VID/PID、模式、WinUSB/udev 与端口路径 |
| `WinError 5` | 同一个 MI_xx 被占用 | 关闭占用方或更换 `--device`/`--usb-port-path` |
| `USBErrorBusy` / claim 失败 | 资源未释放 | 先 `ip link set canX down` 或关闭对应 Python Bus |

完整故障对照可回看《[PythonCAN 使用手册](PythonCAN使用手册.md)》和《[Python API 参考](PythonAPI参考.md)》。






