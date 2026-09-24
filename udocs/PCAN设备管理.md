# PCAN 模式设备管理接口

PCAN 兼容模式下，普通 PCANBasic API 不负责切换本设备的工作模式。VCANDrive 设备通过保留的 CAN FD 管理帧提供只读设备信息、终端电阻配置和模式切换；[设备管理器](设备管理器.md)已经实现自动发现与身份核对。第三方 PCAN 设备不会被识别为本项目设备，也不应发送管理写命令。

## 使用现成工具

在 `python-cli/` 目录运行 `python tools/device_manager.py list` 查看多台设备的模式、固件版本、硬件版本和 UID；执行 `python tools/device_manager.py switch vcan_usb --uid <UID>` 切到 VCAN。目标还可选 `pcan` 或 `vkgs_usb`。Windows 图形界面使用 `python tools/device_manager_ui.py`，单台设备自动选中，多台设备选择对应行。

PCAN 自动发现以只听模式打开空闲通道，读取管理握手和 40 字节设备信息。已被其他进程占用的通道不会被强制打开；旧固件如果不支持设备信息操作，也不会被当作可切换设备。写操作前重新核对 UID 与完整身份信息；切换后按 UID 重新枚举并核对模式和版本。结果不确定时不自动重发。

## 管理帧协议

请求使用扩展 ID `0x1FFFFF00`，响应使用 `0x1FFFFF01`，均为 CAN FD 帧。请求固定 32 字节：`UCM1`（4 字节）、操作（1 字节）、参数（1 字节）、序号（LE16）、非零随机 nonce（LE32）、UID 原始 16 字节、CRC32/ISO-HDLC（LE32，覆盖前 28 字节）。UID 原始值由四个 32 位 UID 字各自按小端排列。操作 1 为握手，2 为设置模式，3 为读取终端，4 为设置终端，5 为读取设备信息。模式参数 0/1/2 分别代表 VCAN/PCAN/GS_CAN；终端参数 0/1 分别代表关/开。

操作 5 的响应返回 40 字节信息：软件版本 u32、硬件版本 u32、UID[4]、UUID[4]，均为小端。管理帧只在设备自身 USB 路径处理，不在物理 CAN 总线上发出；扩展 ID `0x1FFFFF00` 和 `0x1FFFFF01` 应为本项目管理用途保留，应用不得用它们发送业务帧。此握手、nonce、UID、序号和 CRC 用于降低误操作风险，不构成身份认证或加密通道。

需要编写自定义 PCAN 上位机时，可参考 `python-cli/tools/pcan_manage.py` 的消息编码与应答校验。切换模式会保存配置并重启 USB 设备，旧句柄立即失效；必须在重新枚举后验证目标设备身份。
