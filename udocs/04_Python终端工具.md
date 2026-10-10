# Python 终端工具（canctl / can-test）

先按[快速入门](01_快速入门.md)安装对应 Python 后端。本页是 `canctl` 日常命令和 `can-test` 验收入口的速查；要验证自己写的 `can.Bus` 代码，请运行[API 验证](06_API验证.md)。仓库提供两个命令入口：

| 工具 | 文件 | 角色 |
|---|---|---|
| `canctl.py` | `canctl.py` → `tools/canctl.py` | 日常收发、发现、UID/UUID 与版本查询、终端电阻、USB 统计、协议切换 |
| `canctl.py test` | `canctl.py` → `tests/hardware_test.py` | 双通道硬件验收（总线互通/内部回环） |

两个工具都使用公开的 `python-can` 后端（`vcan_usb` 或 `vkgs_usb`），不属于 `python-can` 的可安装 `console_script`。
运行示例默认在 `VCANDrive/python-cli/` 目录。

## 1. 运行入口

三种系统共同入口为 `canctl.py`。Windows 用 `py canctl.py`，Linux/macOS 用 `python3 canctl.py`。后面的参数、测试实现及结果格式一致。所有示例均在 `python-cli/` 执行，不要求激活 venv。

| 操作 | Linux/macOS | Windows |
|---|---|---|
| 安装或更新 | `python3 canctl.py setup` | `py canctl.py setup` |
| 只读诊断 | `python3 canctl.py doctor` | `py canctl.py doctor` |
| 帮助 | `python3 canctl.py --help` | `py canctl.py --help` |
| 完整测试 | `python3 canctl.py test --interface vcan_usb --frames 2000` | `py canctl.py test --interface vcan_usb --frames 2000` |
| 自己的程序 | `python3 canctl.py run demo.py` | `py canctl.py run demo.py` |

顶层帮助、permissions 和 release 不依赖 Python CAN 环境。setup 把依赖装到用户本地目录，避开 HGFS 的 venv 符号链接问题。解释器优先级为 `VCANDRIVE_PYTHON` → 项目内可执行的 `.venv-系统` → 用户默认环境；doctor 显示实际路径。环境目录见[快速入门](01_快速入门.md#2-安装-python-环境)。

也可使用快捷包装：Linux/macOS `./canctl` 和 `./can-test`，Windows PowerShell `.\canctl.cmd` 和 `.\can-test.cmd`。后者等价于 `canctl.py test`；包装脚本不是 pip 的 console script。下面的日常命令以 `python3 canctl.py` 为例，Windows 替换为 `py canctl.py`。

Linux 第一次先 `python3 canctl.py permissions` 预览，再 `sudo python3 canctl.py permissions --apply` 安装规则并重插；以后不加 sudo。BUSY 时运行 doctor 并按提示释放指定板卡。查询已绑定 SocketCAN 的身份可直接读取 sysfs。详见[常见问题](02_常见问题.md)。

## 2. 参数模型（先学会这一层）

`canctl` 支持两个层级：

- 全局参数（`--interface`、`--channel`、`--bitrate`、`--fd`…）
- 子命令（`list`、`info`、`send`、`recv`、`termination`、`stats`、`state`、`usb_mode`）

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

固定 USB 插口时可用 `--usb-port-path` 缩小选择范围；打开后仍须核对 UUID 和通道号。

## 3. 设备发现与定位

Linux：

```bash
python3 canctl.py --interface vcan_usb list
```

Windows：

```powershell
py canctl.py --interface vcan_usb list
```

输出示例：

```text
channel=0 bus=2 address=10 port_path=2.1.4
channel=1 bus=2 address=10 port_path=2.1.4
```

同一组 `bus/address/port_path` 通常是同一物理设备的两路 CAN；`channel` 是板内通道号。`list` 默认只显示通道位置；`list --verbose` 再显示端点地址和包长。发现结果不代表 USB 接口可打开。

### 3.1 只查询板卡身份

以下命令在 `python-cli/` 执行，按当前模式选择一条：

```bash
python3 canctl.py --interface vcan_usb --channel 0 info
python3 canctl.py --interface vkgs_usb --channel 0 info
```

Windows 示例：

```powershell
py canctl.py --interface vcan_usb --channel 0 info
```

`info` 默认输出多行格式化、可解析的精简 JSON：`interface`、`channel`、`uid_hex`、`uuid_hex`、`identity_key`、`sw_version_full_text`、`hw_version_text`、`usb_speed`。需要全部原始字段时，在末尾加 `--json`，例如 `python3 canctl.py --interface vcan_usb --channel 0 info --json`；此时输出格式化的完整 JSON。多设备时加全局 `--usb-bus` / `--usb-port-path`。UID/UUID 各为 32 个十六进制字符。

如果在 Python 程序里使用，直接调用 `vcan_usb_bus.read_device_info(channel=1)` 或已打开 Bus 的 `bus.get_device_info()`，返回字典可按 `info["identity_key"]` 取唯一通道标识；无需解析终端文本。

此命令只查询设备身份，不配置或启动 CAN；`--bitrate`、`--fd`、`--termination` 不影响结果。查询失败会报错，不用 USB 序列号替代 UID/UUID。需有 USB 权限且接口未被占用；SocketCAN 已绑定时按[Linux 身份查询](09_SocketCAN使用手册.md#板卡-uid--uuid-和版本)读取 sysfs。

## 4. 发送（send）

经典 CAN：

```bash
python3 canctl.py --interface vcan_usb --channel 0 --bitrate 1000000 send 0x123 1122334455667788
```

扩展帧、循环发送：

```bash
python3 canctl.py --interface vcan_usb --channel 0 send 0x18DAF110 01020304 --extended
python3 canctl.py --interface vcan_usb --channel 0 send 0x123 A55A --count 100 --gap 0.01
```

RTR：

```bash
python3 canctl.py --interface vcan_usb --channel 0 send 0x123 --rtr --dlc 8
```

CAN FD：

```bash
python3 canctl.py --interface vcan_usb --channel 0 --fd --bitrate 1000000 --data-bitrate 5000000 send 0x123 000102030405060708090A0B0C0D0E0F
python3 canctl.py --interface vcan_usb --channel 0 --fd --bitrate 1000000 --data-bitrate 5000000 send 0x123 000102030405060708090A0B0C0D0E0F --brs
```

Windows 将 `python3 canctl.py` 换成 `py canctl.py`。

`send` 完整度约束：经典帧最大 8 字节，FD 最大 64 字节。

## 5. 接收（recv）

```bash
python3 canctl.py --interface vcan_usb --channel 1 recv
```

```bash
python3 canctl.py --interface vcan_usb --channel 1 recv --count 100 --timeout 2
```

持续监听：

```bash
python3 canctl.py --interface vcan_usb --channel 1 recv --count 0 --timeout 1
```

## 6. 双终端并发表现验证

### 6.1 Linux/macOS

```bash
python3 canctl.py --interface vcan_usb --channel 1 recv --count 60 --timeout 5
```

```bash
python3 canctl.py --interface vcan_usb --channel 0 send 0x123 A55A --count 60 --gap 0.02
```

### 6.2 Windows

```powershell
py canctl.py --interface vcan_usb --channel 1 --termination on recv --count 60 --timeout 5
```

```powershell
py canctl.py --interface vcan_usb --channel 0 --termination on send 0x123 A55A --count 60 --gap 0.02
```

要点：

- 不要同时打开同一个 `channel`；
- 每个 `channel` 建议先后独立释放；
- 多设备时配合 `--usb-port-path` 指定目标。

## 7. 内部回环与硬件验收（can-test）

先按[快速入门](01_快速入门.md)完成发现和第一帧收发。双通道接入同一隔离 CAN 总线后，在 `python-cli/` 目录运行一套完整验收。脚本要求双通道和 FD，固定覆盖混合帧矩阵、双向总线、内部回环、定向序号与重复打开；缺项不会显示部分通过。Windows 示例使用 VCAN；GS_CAN 改为 `vkgs_usb`：

```powershell
py canctl.py test --interface vcan_usb
py canctl.py test --interface vcan_usb --frames 20000 --rounds 3
```

第一条按默认每阶段每发送端 10000 帧运行，第二条重复 3 轮更长的高负载测试。Linux 将入口换成 `python3 canctl.py test`。没有物理接线时，先用 `canctl` 分别检查接口；完整验收必须有两路总线。Linux 内核专项和结果判读见[验收测试路线](10_验证路线.md)及[Python 硬件测试说明](../python-cli/tests/README.md)。

## 8. 设备状态与统计

### 8.1 终端电阻

```bash
python3 canctl.py --interface vcan_usb --channel 0 termination show
python3 canctl.py --interface vcan_usb --channel 0 termination on
python3 canctl.py --interface vcan_usb --channel 0 termination off
```

### 8.2 USB 统计与总线状态

```bash
python3 canctl.py --interface vcan_usb --channel 0 stats --duration 2
python3 canctl.py --interface vcan_usb --channel 0 state --duration 2
```

`stats`/`state` 为当前 interface 的 USB 与总线事件采样，含 `rxerr`、`txerr`、`state` 等。

### 8.3 识别灯（仅 vkgs_usb）

```bash
python3 canctl.py --interface vkgs_usb --channel 0 identify on
python3 canctl.py --interface vkgs_usb --channel 0 identify off
```

`vcan_usb` 后端不支持此命令，执行时会给出明确报错。

## 9. USB 协议模式切换（usb_mode）

该命令会写 Flash 并重启。下面三条是不同当前模式或目标模式的独立示例，只执行符合实际情况的一条：

```bash
# VCAN -> VKGS/gs_usb
python3 canctl.py --interface vcan_usb --channel 0 usb_mode gs_usb

# VCAN -> PEAK 兼容
python3 canctl.py --interface vcan_usb --channel 0 usb_mode peak

# VKGS/gs_usb -> VCAN
python3 canctl.py --interface vkgs_usb --channel 0 usb_mode vcan
```

切换前请先关闭该物理设备上的所有 `Bus` 与 SocketCAN 通道。

## 10. 常见错误速查

| 现象 | 含义 | 建议 |
|---|---|---|
| `Permission denied` | Linux 权限不足 | 运行 permissions 安装规则并重插；日常命令不用 sudo |
| `virtual environment not found` | 所选解释器不存在 | 运行 `canctl.py setup`，再用 doctor 检查 |
| `.venv-win\\Scripts\\python.exe` 不存在 | Windows 环境异常 | 使用统一入口 setup 和 doctor |
| `No module named vcan_usb/vkgs_usb` | 依赖未安装到当前解释器 | 对当前 venv 执行 `pip install -e` |
| `USB device index 0 not found` | 设备定位失败 | 核对 VID/PID、模式、WinUSB/udev 与端口路径 |
| `WinError 5` | 同一个 MI_xx 被占用 | 关闭占用方或更换 `--device`/`--usb-port-path` |
| `USBErrorBusy` / claim 失败 | 资源未释放 | 关闭占用程序；内核绑定用 doctor 提示的 release 命令处理，单独 down 不会解绑 |

完整故障对照可回看《[PythonCAN 使用手册](05_PythonCAN使用手册.md)》和《[Python API 参考](07_PythonAPI参考.md)》。
