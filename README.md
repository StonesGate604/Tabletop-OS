# Tabletop OS

Tabletop OS 是一个把手势感知、HUB75 灯光反馈和 CoreXY 磁力棋子运动结合起来的实体数字桌游平台。

当前 V0.1 软件实现包含双捏合单棋子交互、独立棋子观测验证、PC 最终 RGB 画面、Pi 设备服务与 Arduino 运动固件。机构为舵机升降永磁铁；角度尚未标定，实物动作默认锁定。

## 当前状态

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| Arduino 下位机 | Uno 编译、假 HAL 测试通过；实机待验 | 回零、移动、遥测、停止、看门狗、命令编号同步 |
| Pi Bridge | 主机编译、真实回环协议测试通过；Pi/HUB75 待验 | TCP 控制、TOSF 帧接收、串口桥接、安全联锁 |
| PC Runtime | 仿真与离线视觉测试通过；摄像头实测待验 | 双捏合、升降/定位/拖动序列、RGB、观测验证、操作界面 |
| 最终帧协议 | 已实现并跨 Python/C++ 验证 | UDP 7422，完整 64×64 RGB888、CRC32、会话和超时 |
| 完整实物 Demo | 尚未验收 | 先测安全舵机角度、相机标定和硬件链路 |

## 职责边界

- **PC / Tabletop OS Runtime**：当前管理单棋子交互和观测，合成光标、选择/目标反馈与蓝色运动轨迹；游戏规则和攻击仍是未来扩展。
- **Raspberry Pi**：接收最终像素帧并输出到 HUB75，同时处理可靠控制、状态回传、串口桥接和安全联锁。Pi 不应理解“攻击”“棋子”或“拖影”等游戏概念。
- **Arduino**：执行 CoreXY、限位、回零、舵机/磁铁与实时安全控制。

Pi 默认 `display.mode=frames` 只显示 PC 像素；设为 `diagnostic` 才使用保留的本地圆环/拖影，便于单独排障。

## 仓库结构

```text
pc-runtime/                         Python PC Runtime、相机工具、仿真和测试
pi-bridge/                          Raspberry Pi 服务
firmware/arduino/TabletopOS_Lower_Controller/
                                    Arduino 正式固件
experiments/arduino/                Arduino 单项与集成测试草图
experiments/raspberry-pi/           Raspberry Pi GPIO 测试脚本
protocol/                           控制 schema 和 TOSF v1 帧定义
hardware/                           数据手册与机械尺寸参考
docs/                               项目上下文、路线和研究说明
```

## 最短上手路径

先运行不连接任何硬件的完整模拟（仓库根目录）：

```powershell
python -m venv pc-runtime/.venv
./pc-runtime/.venv/Scripts/python.exe -m pip install -e ./pc-runtime
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate --headless --demo
```

真实硬件部署：

1. 使用 Arduino IDE 打开并上传：
   `firmware/arduino/TabletopOS_Lower_Controller/TabletopOS_Lower_Controller.ino`
2. 在 Pi 上准备并编译 `hzeller/rpi-rgb-led-matrix`。
3. 安装 Pi Bridge：

   ```bash
   sudo apt install -y cmake g++ libboost-dev nlohmann-json3-dev
   sudo bash pi-bridge/tools/install_pi.sh /home/pi/rpi-rgb-led-matrix
   ```

4. 修改 `/etc/tabletop-os/tabletop-os.json` 中的稳定串口路径和面板映射。
5. 启动服务：

   ```bash
   sudo systemctl enable --now tabletop-os-bridge
   ```

详细构建和手工测试方法见 [Pi Bridge 说明](pi-bridge/README.md)，通信边界见 [协议说明](protocol/README.md)。

## 当前链路

- PC → Pi：TCP 7420，用于控制、心跳和状态。
- PC → Pi：UDP 7421，保留的短寿命光标诊断通道，仅在 diagnostic 模式绘制。
- PC → Pi：UDP 7422，TOSF v1 最终像素帧；TCP display.open 绑定会话，过期清屏。
- Pi → Arduino：USB 串口 115200 baud。
- SSH 只用于部署和维护，不承载实时游戏数据。

## 安全与已知限制

- 网络运动命令默认关闭，必须完成低速、空载、限位和急停验证后才能启用。
- 软件停止、心跳和看门狗不能替代物理急停。
- 当前 HUB75 面板存在局部红色通道故障，v0.1 会强制输出 `R=0`。
- 两块 64×32 面板的最终方向与坐标映射仍需在真实 Pi 上校准。
- 不要提交 SSH 密钥、Wi-Fi 凭据、个人设备路径或本机 `.env` 文件。

## 文档

- [中文技术说明书：架构、代码、协议、部署与扩展](docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)
- [本次软件验证记录](docs/VALIDATION_2026-10-16.md)
- [首次实机联调与舵机标定记录表](docs/HARDWARE_COMMISSIONING_ZH.md)
- [项目上下文与诊断基线](docs/Tabletop_OS_Project_Context.md)
- [开发路线](docs/Tabletop_OS_Development_Roadmap.md)
- [中文研究路径](docs/Tabletop_OS_中文路径规划图.md)
- [正式 Arduino 固件说明](firmware/arduino/TabletopOS_Lower_Controller/README_中文.md)

## License

目前尚未选择开源许可证。在明确许可证之前，仓库内容默认保留所有权利。
