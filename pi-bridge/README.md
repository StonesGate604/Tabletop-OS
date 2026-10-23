# Tabletop OS Pi Bridge

Pi Bridge v0.1 负责 TCP/UDP、Arduino 串口、安全联锁以及 HUB75 输出。默认 `display.mode=frames` 显示 PC 最终 RGB 帧；内置圆形光标和蓝色拖影仅在 `diagnostic` 模式用于硬件链路诊断。

TCP 7420 负责控制与显示会话，UDP 7422 使用 TOSF v1 重组 64×64 RGB888；UDP 7421 保留旧诊断光标。详见[协议](../protocol/README.md)和[技术说明书](../docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)。

新版 HOME/MOVE/SERVO 要求正式固件 CONFIG 输出 LAST_TASK_ID；先更新 Uno 固件，避免旧行为和编号不同步。PC 另有舵机未标定锁，Pi 默认网络运动锁仍保留。

## Raspberry Pi 安装

先准备并编译 `hzeller/rpi-rgb-led-matrix`，然后在仓库根目录执行：

```bash
sudo apt install -y cmake g++ libboost-dev nlohmann-json3-dev
sudo bash pi-bridge/tools/install_pi.sh /home/pi/rpi-rgb-led-matrix
```

安装脚本会创建低权限 `tabletop-os` 用户、构建 Release 版本、安装 systemd 服务，并保留已经存在的 `/etc/tabletop-os/tabletop-os.json`。

如果 Pi 上曾安装并启用改名前的服务，升级前先停用它，避免旧、新服务同时占用网络端口、串口和 HUB75：

```bash
sudo systemctl disable --now tessera-bridge
```

然后使用新的安装脚本重新构建。不要复用旧构建目录，也不要用旧配置文件直接覆盖新配置；请把串口路径、面板映射等硬件参数逐项迁移到 `/etc/tabletop-os/tabletop-os.json`。

首次启动前至少要修改：

- `serial.device`：推荐 `/dev/serial/by-id/...`，不要长期依赖 `/dev/ttyACM0`；
- `display.pixel_mapper`：根据两块真实面板的排列和方向校准；
- `display.panel_type`：默认空字符串，不发送特殊初始化；如面板需要 FM6127 初始化，填写 `"FM6127"`。该字段需要支持它的新版 Pi Bridge，旧程序只改 JSON 不会生效；
- `motion.allow_network_commands`：保持 `false`，直到完成全部机构安全验证。

启动：

```bash
sudo systemctl enable --now tabletop-os-bridge
sudo journalctl -u tabletop-os-bridge -f
```

## Windows 主机诊断构建

Windows 构建使用无 HUB75 的 stub 渲染器，可检查网络、配置和程序流程：

需要先让 CMake 能找到 Boost 1.67+ 头文件和 `nlohmann-json`；可以使用 vcpkg、系统包管理器或显式 CMake 工具链。仓库不内置这两个第三方依赖。

```powershell
cmake -S pi-bridge -B pi-bridge/build-host -DTABLETOP_OS_ENABLE_HUB75=OFF
cmake --build pi-bridge/build-host --config Release
./pi-bridge/build-host/Release/tabletop-os-bridge.exe --config ./pi-bridge/config/tabletop-os.json
```

无硬件运行须复制配置并设 serial.enabled=false、display.enabled=false、bind_address=127.0.0.1。Ninja 单配置产物通常直接在 build-host 下。

## 软件测试

```powershell
ctest --test-dir pi-bridge/build-host -C Release --output-on-failure
python pi-bridge/tests/test_bridge.py --bridge pi-bridge/build-host/Release/tabletop-os-bridge.exe -v
```

路径随生成器调整。CTest 包含帧组包和真实固件配假 HAL；黑盒脚本自动运行明确禁用串口/GPIO的本机服务。它们不证明 Pi 电气、USB 或 HUB75 实物通过。

## 从电脑手工测试

send_cursor 需要 deployed 配置设 display.mode=diagnostic 并重启；恢复正式 PC 画面时改回 frames。

```powershell
./pi-bridge/tools/send_control.ps1 -Pi 192.168.1.2 -Command status
./pi-bridge/tools/send_cursor.ps1 -Pi 192.168.1.2 -X 171 -Y 257.5 -Seconds 5 -Gesture grab
```

运动命令默认锁定。先验证串口身份、配置、看门狗、遥测、限位、回零方向和物理急停，再考虑解锁。

## 启用灯板后的串口权限

HUB75 库在初始化后按配置降低 UID/GID，但不会加载目标账号的附加组。仅执行 `usermod -aG dialout tabletop-os` 不保证当前服务进程拥有串口权限。服务文件显式设置 `SupplementaryGroups=dialout`，使降权后仍能访问通常属于 `root:dialout` 的 USB 串口。旧部署可用 systemd override 添加该设置，再执行 daemon-reload 和重启；不用将串口改为所有人可写，也不用关闭降权。

若 `snd_bcm2835` 与硬件脉冲冲突导致初始化退出，首次诊断可以设置 `display.disable_hardware_pulsing=true`，保留系统音频，但可能增加灯板闪烁。帧接收成功不证明实体画面正确，黑屏时还须核对曾经工作的 GPIO/面板驱动参数与供电、接线。

用户提供的历史灯板命令多次使用 `--led-panel-type=FM6127`。本项目现将可选的 `display.panel_type` 传给 `RGBMatrix::Options::panel_type`，并在非空时输出面板类型日志，供重新编译部署后核对。历史命令也是调试记录，不能据此单独证明芯片型号或本次黑屏原因；仍需实际亮屏复测。
