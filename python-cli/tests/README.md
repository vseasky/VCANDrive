# Python USB 硬件验收

先完成[快速入门](../../udocs/快速入门.md)的模式识别、驱动安装和第一帧收发。本目录的 `hardware_test.py` 直接打开设备 USB 接口；双通道 `--mode bus` 要求 CANH 对 CANH、CANL 对 CANL，且总线两端各有 120 Ω 终端。测试会改动设备位时序、终端和工作状态，只在隔离测试总线上运行。完整的 Python 与 Linux 内核测试分工见[验收测试路线](../../udocs/验证路线.md)。

## 1. 选测试范围

| 配置 | 用途 | 覆盖与边界 |
|---|---|---|
| `functional` | 首次验收和日常小批量回归 | 经典 CAN、FD/BRS 关/开，各自的双向总线与内部回环；不跑混合帧专项或长时间压力 |
| `socketcan` | 扩展原始帧矩阵和重复打开 | 在上述阶段外增加 250 kbit/s、1 Mbit/s 的经典混合帧，经典/FD/RTR/标准/扩展矩阵、定向序号和重开；仍不是 Linux 内核验收脚本 |
| `stress` | 长时间收发与缓冲检查 | 同一六类基础阶段，增加每阶段帧数、发送窗口和轮数；不因帧数变大而自动增加 ISO-TP/J1939 等协议覆盖 |
| `--mode loopback --channels 1` | 无总线接线时的单通道自检 | USB 和控制器内部路径；不能证明物理总线与终端正确 |

`--profile` 默认是 `stress`，不是 `functional`；`--frames` 默认值因配置而异。所有示例都显式写出配置与帧数，便于复现结果。`--frames` 表示**每发送端每阶段**的帧数，双向和回环阶段都会分别计数。

## 2. Windows：从小批量到扩展矩阵

在 `python-cli/` 目录运行；`$backend` 必须与设备模式匹配：

```powershell
$py = '.\.venv-win\Scripts\python.exe'
$backend = 'vcan_usb'  # GS_CAN 改为 'vkgs_usb'
& $py .\tests\hardware_test.py --interface $backend --channels 2 --mode bus --profile functional --frames 20 --window 1
```

上面只用于快速功能验收。需要完整混合帧矩阵与重复打开时，至少发送 100 帧/发送端/阶段：

```powershell
& $py .\tests\hardware_test.py --interface $backend --channels 2 --mode bus --profile socketcan --frames 100 --window 32 --reopen-loops 4 --results-json "$env:TEMP\vcandrive-socketcan.json"
```

压力基线另跑一轮，确认收发与队列统计；需要更长时间再提高 `--frames` 和 `--rounds`：

```powershell
& $py .\tests\hardware_test.py --interface $backend --channels 2 --mode bus --profile stress --frames 1000 --window 32 --rounds 1
```

Windows 下另一程序不能同时打开同一 `MI_xx`。若只接了一路，先用 `--channels 1 --mode loopback --profile functional --frames 20 --skip-fd` 自检，并明确记录“未验证物理总线、FD 已跳过”。

## 3. Linux：相同测试配置

在 `python-cli/` 目录、已经释放对应 SocketCAN 内核模块时运行。`can-test` 是仓库内 Bash 入口，依赖此目录的 `.venv-linux`；若执行权限丢失，使用 `sudo bash ./can-test`。以下选择当前模式对应的一个接口名：

```bash
sudo ./can-test --interface vcan_usb --channels 2 --mode bus --profile functional --frames 20 --window 1
sudo ./can-test --interface vcan_usb --channels 2 --mode bus --profile socketcan --frames 100 --window 32 --reopen-loops 4
sudo ./can-test --interface vcan_usb --channels 2 --mode bus --profile stress --frames 1000 --window 32 --rounds 1
```

GS_CAN 模式将 `vcan_usb` 改为 `vkgs_usb`。这里的 `socketcan` 只是测试配置名，仍使用 Python USB 后端；真正的 Linux SocketCAN 驱动专项需运行[内核验收脚本](../../udocs/验证路线.md#4-linux-内核驱动专项)。

## 4. 参数与结果

| 参数 | 默认 | 如何选择 |
|---|---|---|
| `--profile` | `stress` | 首次用 `functional`；矩阵用 `socketcan`；持续流量用 `stress` |
| `--frames` | `functional`: 60；其他：1000 | `socketcan` 至少 100 才完整走过经典/FD 混合矩阵；长压可增至 10000 |
| `--window` | `functional`: 1；其他：32 | 等待收齐前的发送批次上限，不是 USB 传输并发数 |
| `--rounds` | 1 | 完整测试轮数；长压可设 5 |
| `--reopen-loops` | 4 | `socketcan` 专项中重复打开次数 |
| `--bitrate` / `--data-bitrate` | 1000000 / 5000000 | 标称/FD 数据速率；接线两端必须一致 |
| `--skip-fd` | 关闭 | 明确跳过 FD，结果是部分覆盖 |
| `--results-json` | 无 | 将阶段、TX/RX、异常和覆盖空缺写到仓库外的文件 |

成功时，每阶段各方向的 `TX` 与 `RX` 及载荷字节数应一致，`invalid/duplicate/reorder=0`、`app_drop=0`、`overflow=0`，控制器保持 `ACTIVE`，最后显示 `TEST PASSED`。USB transfer 次数不等于 CAN 帧数。`socketcan` 末尾会列出相对 Linux 内核专项仍未覆盖的项目；它们不会因为 `TEST PASSED` 而消失。任何阶段失败时，脚本不重发不确定的帧，并保留首次异常和少量后续样本；不能只用 `RX` 差值断言物理丢帧。

退出码 0 表示**所选阶段**通过，1 为验收失败，2 为参数或运行错误。缺少 FD 能力时，默认会失败；只想验经典 CAN 必须显式使用 `--skip-fd`。脚本不代替业务协议、物理故障注入或 Linux 内核专项测试。
