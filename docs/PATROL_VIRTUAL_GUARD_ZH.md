# Patrol：虚拟守卫与红色显示更新（2026-11-04）

当前本地配置为一枚实体玩家（Tag 0）和一个虚拟守卫，8 列×12 行，每格 40×40 mm。守卫以红色格子显示，自动沿原路径巡逻并进行视野结算。启动只等待实体玩家，不等待 Guard 标签、不生成虚假的视觉观测、不发送电机或舵机指令。

当前配置已保存为 [2026-11-04 进度快照](../pc-runtime/config/checkpoints/2026-11-04/README.md)，包含本机配置与标定文件，并提供恢复命令。用户已确认 Tag 0 玩家识别和手部接近预览可以正常触发。

## 配置

本机 `pc-runtime/config/local-patrol.json` 已设置 `game.guard_mode="virtual"`，`pieces` 中只保留玩家 Tag 0，移除了守卫的物理绑定。守卫保留逻辑 ID、起点和巡逻路径。原来的双实体模式仍可使用：将模式改回 `physical`，并添加使用不同 Tag ID 的守卫绑定。虚拟守卫与 `--hardware-motion` 同时使用会报错。

代码更新后需关闭旧 Python 窗口并重新启动；仅按 R 不会重新加载 Python 源代码。实体玩家放到蓝色起点 `(11,0)`，目标为 `(11,4)`、出口为 `(11,7)`。不用的实体守卫可移出棋盘，避免遮挡显示或产生未注册标签提示。

```powershell
Set-Location -LiteralPath "C:\Users\rog\Documents\Tabletop OS"
.\pc-runtime\.venv\Scripts\python.exe -m tabletop_os --app patrol --config .\pc-runtime\config\local-runtime.json --patrol-config .\pc-runtime\config\local-patrol.json --led
```

窗口显示 `Mode: VIRTUAL`、`Guard: VIRTUAL`。玩家操作方式不变：手靠近预览，移动到合法格，放下并移开手；随后守卫自动前进一步。

手部光标同时显示在电脑棋盘、LED 及摄像头预览中：青色十字和白色中心点表示当前手部投影位置，状态栏 `Hand` 显示桌面毫米坐标，离开棋盘时显示 `outside board`。无手或输入过期后清除光标，光标不会提交行动或改变地图。此功能只需重启电脑端 Python，树莓派和 Arduino 无需更新。本机接近范围已设为进入 80 mm、退出 120 mm。

本机标签遮挡宽限 `proximity.tag_loss_grace_s` 已设为 5 秒。玩家标签曾被识别后，短暂遮挡时保留最后观测的位置和逻辑身份，手靠近该位置仍可开启移动预览；电脑棋盘显示黄色虚线圈及 `player HOLD`，状态栏列入 `Held`。位置不会跟随手部光标移动，也不会用缓存位置确认落子；重新露出标签、稳定放置并移开手后才确认移动。超过宽限仍未识别时进入恢复状态，不消耗回合。首次启动仍须将 Tag 0 无遮挡放在蓝色起点，等待 `Phase: PLAYER_TURN`，未识别过的标签不会凭逻辑起点生成缓存。更改标签配置或重开后清空旧缓存。

## 红色输出与树莓派更新

PC 已发送完整 RGB，Pi 源码 `pi-bridge/src/hub75_renderer.cpp` 已移除应用帧的红色清零。旧诊断光标和拖影继续使用原来的绿蓝配色。Arduino 固件不变。

现有树莓派的实物 RGB 分区检查中观察到蓝/绿区域，未观察到红色区域，与旧版软件屏蔽一致；更新后的红色实物显示尚待部署后复测。

电脑端已准备：

- `outputs/tabletop-os-patrol-rgb.tar.gz`：当前 Bridge 源码与部署脚本。
- `outputs/deploy-patrol-rgb.ps1`：上传并启动树莓派更新。
- `outputs/raspberry-pi-known-hosts`：从用户 Xshell 中既有公开主机密钥生成，严格核对同一设备，不包含私钥或密码。

以下 PowerShell 上传脚本为早期部署方式；本机执行曾因带空格的主机密钥文件路径失败，尚未修复，不作为当前推荐命令。实际成功部署由用户通过 Xshell/Xftp 完成，见验证记录。

原命令（保留用于排查）：

```powershell
& "C:\Users\rog\Documents\Tabletop OS\outputs\deploy-patrol-rgb.ps1"
```

脚本连接 `stonesgate@10.10.10.2`，在 SSH 提示时由用户自行输入密码。输入密码通常不会显示字符；密码不需要发到聊天中。树莓派执行 `sudo` 时也可能需要输入密码。

更新流程先定位已安装的 `rpi-rgb-led-matrix` 库并编译，再备份旧 Bridge 与 `/etc/tabletop-os/tabletop-os.json`，停止服务、替换程序并启动；失败时尝试恢复旧程序。现有配置、运动开关和 Arduino 固件不会被修改。若自动定位库失败，脚本会在改变设备前停止，需根据提示提供实际库路径。

成功时显示 `RGB Bridge update complete`。之后重新启动上面的 Patrol 命令，检查红色守卫格和玩家接近预览。

## 验证记录

- 当前 Git 进度保存前完整 PC 回归测试：**219 项通过**（34.224 秒），Tag 0 本机配置校验通过，五份配置快照与当前文件一致。
- 完整 PC 回归测试 **214 项通过**，包含新增 16 项虚拟守卫用例。
- 最后位置保留更新：接近、虚拟守卫及 Runtime 的 **56 项测试通过**，覆盖遮挡发生在接近停留完成前、过期/未识别标签不能开启预览、缓存不能提交落点、重新识别后只提交一次，以及超过 5 秒进入恢复状态。另已通过源文件语法编译；实际手部遮挡表现待用户重启后验证。
- 当前 8×12 本地配置通过校验；无需 Guard 观测的完整模拟以 7 回合获胜。
- 已用真实摄像头及 LED 会话打开 `VIRTUAL` 模式；玩家 Tag 0 可以识别。
- Pi HUB75 源文件通过 MSVC C++17 编译语法检查，检查时使用仅提供 API 声明的替身头文件；不将此视为实际 GPIO 驱动链接或 Pi 编译成功。
- 登录、上传和树莓派部署由用户完成；目前没有可供代理使用的免密 SSH 凭据。
- 2026-11-04 用户提供 Xshell 输出：Pi 上使用 GCC 14.2.0、Boost 1.83 和 `/home/stonesgate/projects/rpi-rgb-led-matrix` 成功编译了实际 HUB75 版本，部署脚本报告 `RGB Bridge update complete`。旧程序和配置备份位于 `/home/stonesgate/tabletop-os-patrol-rgb/backup-20261104-234640`。设备配置与 Arduino 固件未修改。
- 仍需启动 Patrol 核对红色守卫格在实物上的显示；编译和服务部署成功不能替代颜色及对齐验收。本次没有自动操作电机。
