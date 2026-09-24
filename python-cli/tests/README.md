# Python API 测试

测试使用两种后端的公开 Bus 类，直接访问 USB。运行前关闭其他测试进程并释放
内核驱动对目标接口的占用；接口 DOWN 本身不释放 USB。按实际加载模块使用
`sudo rmmod vcan_usb` 或 `sudo rmmod vkgs_usb`。测试不会自动卸载驱动。

## Linux

在 `python-cli/` 目录执行，设备应处于对应 USB 模式：

```bash
# 小批量功能验收
sudo ./tests/vkgs_usb/test_vkgs_usb.sh --profile functional --channels 2

# 压力测试：每发送端每阶段 10000 帧，5 轮
sudo ./tests/vkgs_usb/test_vkgs_usb.sh --channels 2 --frames 10000 --window 32 --rounds 5

# VCAN 模式使用对应入口
sudo ./tests/vcan_usb/test_vcan_usb.sh --channels 2 --frames 10000 --window 32 --rounds 5
```

两种入口共用 `hardware_test.py`，也可用 `./can-test --interface vkgs_usb ...`。
共享总线要求 CANH-CANH、CANL-CANL 相连，默认启用两端终端电阻。

## Windows

```powershell
.\.venv-win\Scripts\python.exe .\tests\hardware_test.py --interface vkgs_usb --channels 2 --frames 10000 --window 32 --rounds 5
```

VCAN 将 interface 改为 `vcan_usb`。同一接口不能被其他程序占用。

## 参数

| 参数 | 默认 | 含义 |
|---|---|---|
| `--profile` | stress | functional 默认 60 帧/窗口 1；stress 默认 1000 帧/窗口 32 |
| `--frames` | 取决于 profile | 每发送端每阶段帧数，1..2^32 |
| `--window` | 取决于 profile | 每发送端等待接收确认前的批次上限，1..1024 |
| `--rounds` | 1 | 完整测试轮数，每轮重新打开设备 |
| `--mode` | auto | 双通道共享总线后做独立回环；单通道 auto 只回环；loopback 强制只回环 |
| `--bitrate` / `--data-bitrate` | 1000000 / 5000000 | 标称/FD 数据速率 |
| `--timeout-ms` | 2000 | 发送/USB 超时 |
| `--receive-timeout` | 2 | 每窗口收齐等待秒数 |
| `--drain-time` | 0.2 | 末尾继续接收，检测迟到重复帧 |
| `--skip-fd` | 关闭 | 跳过 FD，结果标记部分覆盖 |

更多设备选择参数见 `--help`。无逐帧固定限速，不自动重试或降速。
窗口不是并发 USB 请求数；每次 send 仍等待 USB 完成。

## 验收与退出

覆盖经典 CAN、FD/BRS-off、FD/BRS-on，各阶段交替标准/扩展格式；共享总线测试后
重开设备进行独立回环。检查序号、顺序、重复、载荷、DLC、标志和队列溢出。
输出各方向 TX/RX、USB 统计差值、CAN 缓存状态、耗时和吞吐。USB 完成次数不等于 CAN 帧数。

阶段失败立即停止后续阶段并释放接口。0 表示所选测试通过，1 为验收失败，2 为参数或
运行错误。部分覆盖通过不等于完整覆盖；速率不代表硬件理论上限。
本套件不替代 ISO-TP/J1939、RTR、bus-off 故障注入或目标总线负载测试。

## 无硬件回归

```bash
PYTHONDONTWRITEBYTECODE=1 .venv-linux/bin/python -m unittest discover -s tests/unit -v
```

公开安装和验收说明见[文档导航](../../udocs/README.md)。

测试开始时打印每通道时钟和 FD 能力。设备不支持 FD 时，默认套件会报错，
可明确指定 `--skip-fd` 验证经典 CAN；不会自动降级为部分覆盖。
