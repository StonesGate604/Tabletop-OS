# Tabletop OS

Tabletop OS 是一个把手势感知、HUB75 灯光反馈和 CoreXY 磁力棋子运动结合起来的实体数字桌游平台。

当前 V0.1 软件实现包含双捏合单棋子交互、独立棋子观测验证、PC 最终 RGB 画面、Pi 设备服务与 Arduino 运动固件。机构为舵机升降永磁铁；本机于 2026-10-26 实测最高吸附角度 81°、最低释放角度 118°。截至 2026-10-30，开发者确认已在真实设备上多轮成功执行磁力吸附 → 拖动 → 释放的完整动作；长期连续运行、全桌精度及异常场景的系统化验收仍需记录。

## 当前状态

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| Arduino 下位机 | 编译和假 HAL 测试通过；运动链路已有实机验证 | 回零、移动及磁力取放相关控制已用于现场测试；故障恢复等完整安全矩阵仍需验收 |
| Pi Bridge | 协议测试通过；PC→Pi→Arduino 与灯板反馈已现场联动 | TCP 控制、TOSF 帧接收、串口桥接、安全联锁；三屏映射及长期稳定性仍需进一步核验 |
| PC Runtime | 仿真测试通过；磁力吸附→拖动→释放已多轮实测 | 双捏合、标签遮挡保持、灯板/机械双映射、118°/81° 取放序列；位置观测与异常恢复仍需完整记录 |
| 最终帧协议 | 已实现并跨 Python/C++ 验证 | UDP 7422，完整 64×96 RGB888、CRC32、会话和超时 |
| 实体棋子运动 Demo | 核心取放动作已多轮成功；尚无正式游戏 | 开发者确认实机吸附、拖动、释放可用；后续量化重复成功率、升降稳定时间、安全间距、相机精度与长时间运行表现 |

## 2026-10-30 实机状态补充

- **已由开发者现场确认**：实体装置成功重复执行磁铁吸附、CoreXY 拖动和释放棋子的完整动作，已不属于「只通过离线仿真」阶段。
- **并不等同于已完成的测试**：目前仓库未记录标准化测试轮数、成功率、全桌精度、多人/多棋子避碰和故障恢复统计。旧验证文档中的「待验收」对应其撰写时的测试进度，不应覆盖这次新的实测反馈。
- **下一个重点**：在现有可靠移动能力上实现第一个小型游戏 Application，并开展完整回合的现场测试。

## 职责边界

- **PC / Tabletop OS Runtime**：当前管理单棋子交互和观测，合成光标、选择/目标反馈与蓝色运动轨迹；游戏规则和攻击仍是未来扩展。
- **Raspberry Pi**：接收最终像素帧并输出到 HUB75，同时处理可靠控制、状态回传、串口桥接和安全联锁。Pi 不应理解“攻击”“棋子”或“拖影”等游戏概念。
- **Arduino**：执行 CoreXY、限位、回零、舵机/磁铁与实时安全控制。

Pi 默认 `display.mode=frames` 只显示 PC 像素；设为 `diagnostic` 才使用保留的本地圆环/拖影，便于单独排障。

## 游戏 Application 接入现状

当前 PC 代码已有 `TabletopSystem` 服务门面（`pc-runtime/src/tabletop_os/system/session.py`），但**尚无正式游戏引擎或自动注册游戏插件的 API**；`cli.py` 目前只接受 `gesture-drag`、`single-piece` 两种应用。游戏需要增加新的 Application、启动分支和游戏画面渲染，不能直接使用现有 CLI 参数启动。

现有应用可复用以下服务接口：

- `system.tracking.visible_tags`：当前帧的新鲜 AprilTag 观测（每项包含 `piece_id`、`position`、`timestamp`）；`system.tracking.tags` 和 `display_tags` 可提供缓存/显示位置，但缓存不能被视为真实棋子移动已完成。
- `system.request_move(piece_id, target_display, now)`：请求移动指定实体棋子，目标使用**64×96 灯板的逻辑像素坐标**（`Point`），由系统转换成机械毫米坐标，并实施安全检查及吸附/拖动/释放序列。需要启用经过标定的取放模式、有新鲜机械观测且设备就绪；只受理任务，不会立即完成。
- `system.motion_view`：查看 `enabled`、`phase`、`stage`、`error`、`selected_id`、`target`、`device`；**只有视觉验证到位后才进入 `DONE`**。结束后需显式 `system.reset()` 才能开始下一轮运动。
- `system.snapshot()`、`system.display_status()`：查询设备状态和显示链路状态；`system.cancel()`、`system.stop()`、`system.home(now)`、`system.reset()`：运动控制与恢复入口。
- `system.publish(scene)`：目前只支持手势应用的 `Scene`（指针、标签、选中、目标）和对应的 `pointer_frame`，还**不支持游戏棋盘、地形或动画场景的通用渲染接口**。

每一帧的既有调用顺序见 `pc-runtime/src/tabletop_os/gesture_ui.py`：`camera.analyze(...)` → `system.update(...)` → `app.update(...)` → `app.scene(...)` → `system.publish(...)`。新游戏最好沿用此边界，由应用维护回合、规则、玩家/NPC 行动意图，系统负责真实观测、运动与设备通信。参见 [PC 主程序与系统/应用分层](docs/PC_LAYERS_ZH.md)。

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

PC 已统一为一个启动入口，系统服务与交互应用分开。当前相机/手势/标签/灯板/滑台联动的启动方法见[PC 主程序与系统/应用分层](docs/PC_LAYERS_ZH.md)。无参数启动为手势应用预览；以下 `--simulate` 命令继续运行原单棋子仿真。

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
- 三块 64×32 面板的最终方向与坐标映射仍需在真实 Pi 上校准。
- 不要提交 SSH 密钥、Wi-Fi 凭据、个人设备路径或本机 `.env` 文件。

## 文档

- [中文技术说明书：架构、代码、协议、部署与扩展](docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)
- [PC 主程序与系统/应用分层](docs/PC_LAYERS_ZH.md)
- [本次软件验证记录](docs/VALIDATION_2026-10-16.md)
- [摄像头、手势、灯板与磁吸滑台联调记录](docs/VALIDATION_2026-10-24.md)
- [首次实机联调与舵机标定记录表](docs/HARDWARE_COMMISSIONING_ZH.md)
- [项目上下文与诊断基线](docs/Tabletop_OS_Project_Context.md)
- [开发路线](docs/Tabletop_OS_Development_Roadmap.md)
- [中文研究路径](docs/Tabletop_OS_中文路径规划图.md)
- [正式 Arduino 固件说明](firmware/arduino/TabletopOS_Lower_Controller/README_中文.md)

## License

目前尚未选择开源许可证。在明确许可证之前，仓库内容默认保留所有权利。
