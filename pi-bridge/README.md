# Tessera Pi Bridge

Pi Bridge v0.1 负责 TCP/UDP、Arduino 串口、安全联锁以及 HUB75 输出。当前内置圆形光标和蓝色拖影只用于硬件链路诊断。

## Raspberry Pi 安装

先准备并编译 `hzeller/rpi-rgb-led-matrix`，然后在仓库根目录执行：

```bash
sudo apt install -y cmake g++ libboost-dev nlohmann-json3-dev
sudo bash pi-bridge/tools/install_pi.sh /home/pi/rpi-rgb-led-matrix
```

安装脚本会创建低权限 `tessera` 用户、构建 Release 版本、安装 systemd 服务，并保留已经存在的 `/etc/tessera/tessera.json`。

首次启动前至少要修改：

- `serial.device`：推荐 `/dev/serial/by-id/...`，不要长期依赖 `/dev/ttyACM0`；
- `display.pixel_mapper`：根据两块真实面板的排列和方向校准；
- `motion.allow_network_commands`：保持 `false`，直到完成全部机构安全验证。

启动：

```bash
sudo systemctl enable --now tessera-bridge
sudo journalctl -u tessera-bridge -f
```

## Windows 主机诊断构建

Windows 构建使用无 HUB75 的 stub 渲染器，可检查网络、配置和程序流程：

需要先让 CMake 能找到 Boost 1.67+ 头文件和 `nlohmann-json`；可以使用 vcpkg、系统包管理器或显式 CMake 工具链。仓库不内置这两个第三方依赖。

```powershell
cmake -S pi-bridge -B pi-bridge/build-host -DTESSERA_ENABLE_HUB75=OFF
cmake --build pi-bridge/build-host --config Release
./pi-bridge/build-host/Release/tessera-bridge.exe --config ./pi-bridge/config/tessera.json
```

## 从电脑手工测试

```powershell
./pi-bridge/tools/send_control.ps1 -Pi 192.168.1.2 -Command status
./pi-bridge/tools/send_cursor.ps1 -Pi 192.168.1.2 -X 171 -Y 257.5 -Seconds 5 -Gesture grab
```

运动命令默认锁定。先验证串口身份、配置、看门狗、遥测、限位、回零方向和物理急停，再考虑解锁。
