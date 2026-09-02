# VCANDrive

VCANDrive 是 HPMicro USB-CAN 硬件的配套驱动文档与配套软件仓库，覆盖：

- Linux SocketCAN 内核驱动（`kernel/`）
- Python-can 后端驱动包（`python-cli/`）
- 驱动验证与问题排查文档（`docs/`）

仓库不包含硬件固件镜像；若需比对协议、时序或寄存器行为，可结合同级 `../03.FirmWare` 目录中的固件资料。

## 版本与定位

VCANDrive 同时支持两种 USB 协议模式：

| 模式 | USB VID:PID | 内核入口 | Python 接口名 | 安装包 |
|---|---|---|---|---|
| VCAN | `1d50:6080` | `vcan_usb` | `vcan_usb` | `vcan-usb` |
| VKGS / gs_usb 扩展 | `1d50:606f` | `vkgs_usb` | `vkgs_usb` | `vkgs-usb` |

文档中将“VCAN 固件”“VKGS 固件”“CAN 接口”按同一口径描述，避免在不同文档中出现
混用别称。

## 文件索引（先读顺序）

- `docs/README.md`：文档入口与推荐阅读顺序（建议先读）。
- `docs/PythonCAN使用手册.md`：Python backend 安装、运行时依赖、双端口并发模型。
- `docs/PythonAPI参考.md`：Python API 参数、异常、扩展方法和多通道约束。
- `docs/Python终端工具.md`：`canctl`/`can-test` 命令参数与常见用法。
- `docs/SocketCAN使用手册.md`：Linux SocketCAN 安装与 `candump`/`cansend` 验证流程；
  C SocketCAN 示例集中在“C API 快速示例”小节。
- `docs/驱动特殊API说明.md`：USB 厂商控制请求、帧格式差异、协议切换。
- `docs/优化记录.md`：本项目可见行为变更和验证说明。

## 常见工作流

1. 先确认当前固件模式与 VID:PID；
2. 选择 **同一种后端**（Linux SocketCAN 或 Python 后端）；
3. 通过 `channel`（USB interface 编号）和 `port_path`（建议）来稳定定位硬件；
4. 多协议切换请使用 `usb_mode`，切换后设备会重启并重新枚举；
5. 切换 backends 之前，先关闭所有已打开的 Bus / can 接口，避免并发占用。

## 开发注意

本仓库在本轮优化中只补充文档与说明，不修改驱动源码和已发布的核心行为。

- 需要修改内核或 Python 运行时代码时，按模块独立改动；
- 如果你是首次接入，优先确认：
  - Linux 下 udev 与权限策略；
  - Windows 下每个 MI_xx 是否已绑定 WinUSB；
  - `bus/address/port_path` 三者的组合是否唯一指向目标设备；
- 有关错误处理与重试行为，请使用终端日志和 `tests/unit` 回归用例核验。

## 目录快速入口

- `kernel/vcan_usb/`、`kernel/vkgs_usb/`：内核驱动源码与编译说明
- `kernel/tests/`：驱动验收脚本
- `python-cli/vcan_usb/`、`python-cli/vkgs_usb/`：python-can 后端源码
- `python-cli/tests/`：单元测试和硬件验收脚本

## 许可证

仓库许可更新为 GNU GPL，详见根目录 `LICENSE`。如有二次分发或二次开发，请保留原始版权
与协议声明。

