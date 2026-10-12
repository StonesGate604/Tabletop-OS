# Tabletop OS

Tabletop OS 是一个把手势感知、HUB75 灯光反馈和 CoreXY 磁力棋子运动结合起来的实体数字桌游平台。

当前仓库首先打通一条最小硬件链路：电脑发送交互与运动意图，Raspberry Pi 负责网络、HUB75 和串口桥接，Arduino 负责实时机构控制与独立安全停止。

## 当前状态

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| Arduino 下位机 | 已实现，仍需完整实机标定 | 支持回零、移动、状态遥测、停止与主机看门狗 |
| Pi Bridge v0.1 | 已完成主机端构建与协议测试，待 Pi/HUB75 实机验证 | 当前包含本地光标和蓝色拖影诊断渲染 |
| PC Runtime | 尚未实现 | 未来负责摄像头、手势、游戏规则、动画与最终画面合成 |
| 最终帧协议 | 待实现 | 计划由 PC 向 Pi 发送完整的 64×64 像素帧 |

## 职责边界

- **PC / Tabletop OS Runtime**：理解棋子和游戏，生成光标、移动轨迹、攻击特效、动画以及最终画面。
- **Raspberry Pi**：接收最终像素帧并输出到 HUB75，同时处理可靠控制、状态回传、串口桥接和安全联锁。Pi 不应理解“攻击”“棋子”或“拖影”等游戏概念。
- **Arduino**：执行 CoreXY、限位、回零、舵机/磁铁与实时安全控制。

Pi Bridge v0.1 目前仍会根据光标数据和 Arduino 位置在本地绘制圆环与蓝色拖影。这是为了验证坐标、面板和运动链路而保留的诊断基线，不是最终游戏渲染架构。

## 仓库结构

```text
pc-runtime/                         PC 程序的职责与后续入口（代码尚未开始）
pi-bridge/                          Raspberry Pi 服务
firmware/arduino/TabletopOS_Lower_Controller/
                                    Arduino 正式固件
experiments/arduino/                Arduino 单项与集成测试草图
experiments/raspberry-pi/           Raspberry Pi GPIO 测试脚本
protocol/                           当前协议入口和后续帧协议位置
hardware/                           数据手册与机械尺寸参考
docs/                               项目上下文、路线和研究说明
```

## 最短上手路径

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
- PC → Pi：UDP 7421，v0.1 暂用于短寿命光标诊断数据；后续将替换或扩展为最终像素帧通道。
- Pi → Arduino：USB 串口 115200 baud。
- SSH 只用于部署和维护，不承载实时游戏数据。

## 安全与已知限制

- 网络运动命令默认关闭，必须完成低速、空载、限位和急停验证后才能启用。
- 软件停止、心跳和看门狗不能替代物理急停。
- 当前 HUB75 面板存在局部红色通道故障，v0.1 会强制输出 `R=0`。
- 两块 64×32 面板的最终方向与坐标映射仍需在真实 Pi 上校准。
- 不要提交 SSH 密钥、Wi-Fi 凭据、个人设备路径或本机 `.env` 文件。

## 文档

- [项目上下文与诊断基线](docs/Tabletop_OS_Project_Context.md)
- [开发路线](docs/Tabletop_OS_Development_Roadmap.md)
- [中文研究路径](docs/Tabletop_OS_中文路径规划图.md)
- [正式 Arduino 固件说明](firmware/arduino/TabletopOS_Lower_Controller/README_中文.md)

## License

目前尚未选择开源许可证。在明确许可证之前，仓库内容默认保留所有权利。
