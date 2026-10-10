# Python CAN USB 后端

Windows、Linux、macOS 使用同一个入口和同一套测试参数。在本目录执行；先看[从零开始的快速入门](../udocs/01_快速入门.md)，报错看[常见问题](../udocs/02_常见问题.md)。

| 模式 | USB ID | 后端 |
|---|---|---|
| GS_CAN | `1d50:606f` | `vkgs_usb` |
| VCAN | `1d50:6080` | `vcan_usb` |

Linux/macOS：

```bash
python3 canctl.py setup
python3 canctl.py doctor
python3 canctl.py --interface vkgs_usb list
python3 canctl.py --interface vkgs_usb --channel 0 info
```

Windows（每路 MI 接口先绑定 WinUSB）：

```powershell
py canctl.py setup
py canctl.py doctor
py canctl.py --interface vkgs_usb list
py canctl.py --interface vkgs_usb --channel 0 info
```

Linux 首次配置普通用户权限：`sudo python3 canctl.py permissions --apply`，然后重新插拔设备。若 doctor 显示内核占用，按输出用 release 释放选定板卡；日常命令不用 sudo。macOS 先安装原生 libusb，例如 `brew install libusb`。

双通道接线完成后运行 `python3 canctl.py test --interface vkgs_usb --frames 2000`；Windows 只替换开头为 `py`。测试会配置两路终端、位率并发送经典/FD 帧，详见[测试说明](tests/README.md)。仅 list 成功不代表通道可打开或总线能收发。

setup 默认安装两个独立后端，也可指定 `--interface vkgs_usb`。自动使用用户本地虚拟环境，避免共享盘符号链接问题；已有解释器可用 `VCANDRIVE_PYTHON` 指定。源码目录需要保留，因为安装采用 editable 模式。

快捷包装为 Linux/macOS 的 `./canctl`、`./can-test` 和 Windows 的 `.\canctl.cmd`、`.\can-test.cmd`。运行自己的脚本用 `canctl.py run demo.py`，无需激活环境。

协议编解码可独立导入，Bus 在使用时加载。API、USB 速率来源、设备定位与会话规则见[Python CAN 手册](../udocs/05_PythonCAN使用手册.md)和[API 参考](../udocs/07_PythonAPI参考.md)。脚本为仓库工具，不随两个驱动包发布。
