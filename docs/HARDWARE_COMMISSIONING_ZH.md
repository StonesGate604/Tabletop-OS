# V0.1 首次实机联调记录表

当前机构由用户确认为“舵机升降永磁铁”。2026-10-26 已通过手动、小步进舵机测试测得两个机构端点，并将移动滑台（moving carriage）尺寸从总跨度中扣除，复测行程为 X=298 mm（`344−46`）、Y=466 mm（`514−48`）；这不代表整套实物闭环已经验收。共享配置仍默认 `gripper.calibrated=false`，本机被忽略的 `local-runtime.json` 已写入实测角度。旧相机毫米标定不能沿用。Pi 的网络运动锁仍应在其余安全项目确认后再启用。

## 1. 先记录设备和安全角度

| 项目 | 实测填写 |
| --- | --- |
| 日期、操作者、Uno 串口 / Pi 稳定设备路径 | 待填写 |
| 舵机型号、独立供电、共地 | 待填写 |
| 最高吸附角度 engage_angle | **81°（2026-10-26 实测）** |
| 最低释放角度 release_angle | **118°（2026-10-26 实测）** |
| 升降所需稳定时间 settle_s（默认 0.6 s，需实测） | 待填写 |
| 磁铁处于最低释放位置后与全部棋子的安全间距 | 待填写 |
| PWM 关闭或掉电后机构是否会下落 | 待填写 |

停止 Pi Bridge、Arduino IDE 串口监视器等占用者。首次测试应卸下连杆或确认机构不碰撞，再用正式固件逐个小范围角度试验，不自动扫描 0–180°：

```powershell
./pc-runtime/tools/calibrate_servo.ps1 -Port COM3 -Angle 90 -MechanismClear
```

COM3 和 90 只是显式命令示例，不是已确认安全的设备路径和角度。脚本先确认正式固件身份、DISABLE 电机，再只测试指定角度，默认 2 秒后 SERVO_OFF；这不切舵机电源，也不保证机械释放。不要在没有确认连杆间距时尝试范围端点。

本机被忽略的 `pc-runtime/config/local-runtime.json` 已填写 `release_angle=118`、`engage_angle=81` 并设 `calibrated=true`；完整流程速度设为 45 mm/s，`settle_s=0.6` 仍是默认值，需在完整升降测试中确认。仿真中的 60/120 仅是虚拟角度，不能发送给实物。

## 2. 正式固件与电机

- 核对当前 1/16 细分、GT2、16 齿轮、100 steps/mm，X/Y 行程 298/466 mm（分别为 `344−46`、`514−48`，减数为 moving carriage 尺寸），两个起点限位方向均 -1。
- IDENTIFY 应返回 `READY TABLETOP_OS_LOWER 1`；CONFIG 应含 `LAST_TASK_ID`。
- 先逐个手动按限位，STATUS 的 IO 应随之变化；检查物理急停。
- 首次低速空载 HOME 前，明确让磁铁处于 118° 最低释放位置。PC 的 HOME 会先 release、等待 settle，再 HOME。固件现在保持已有舵机指令，Pi 每 2 秒刷新；请验证回零全程不意外升到吸附位置。
- 等 COMPLETED HOME 后，先以 2 mm/s 在中心附近移动 10–20 mm，测量方向、距离和停止距离，再扩大范围。
- 测试 STOP、硬限位、主机失联、USB 拔插、MCU 复位；参考无效后必须重新 HOME。

## 3. 灯板与相机

- Pi `display.mode=diagnostic`，保持运动锁，先用 send_cursor 测四角和中心；校准两块面板的 Remap。恢复 `mode=frames` 后用 PC 最终帧。
- 红色当前被强制屏蔽；不要用红色亮不亮判定新帧是否到达。
- 固定相机、裁切、缩放和桌面后做两套标定，全程不能移动相机。机械标定运行 `calibrate_camera.py --config ./pc-runtime/config/local-runtime.json`，依次点击移动滑台中心可达区的 `(0,0)`、`(298,0)`、`(298,466)`、`(0,466)` 四角；保存到 `local-calibration.json`。
- 显示标定启动 `gesture-drag` 时不要加 `--reuse-map`，依次点击 320×480 mm 实际发光区域的左上、右上、右下、左下，按 `S` 保存 `local-display-map.json`。之后才可使用 `--reuse-map`。
- PC 通过共享相机平面在两套标定之间转换，不会把 298×466 mm 机械范围直接铺满灯板；居中安装时应保留左右各 11 mm、上下各 7 mm 的灯面不可达边缘。
- 在独立的中间位置验证毫米误差；四个标定点拟合成功不证明全桌误差合格。
- AprilTag 默认 36h11、ID=0，保持白边并平放；测试手遮挡、反光、标签丢失、目标边缘和镜头高度。

## 4. 完整动作验收

角度、回零、限位、相机与显示都通过后，才人工启用 Pi `motion.allow_network_commands=true` 并重启。PC 使用 camera 模式和 local-runtime 配置：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --app gesture-drag --config ./pc-runtime/config/local-runtime.json --reuse-map --pick-place --speed 45 --log ./pc-runtime/logs/hardware.log
```

启动后先按 `H`，确认 118° 释放并完成回零。第一次捏合只选中，释放后移动光标；第二次捏合才提交。应看到 release → seek → engage → drag → release → VERIFYING，只有标签新观测连续满足误差才进入 DONE；按 `C` 清除结果后才能开始下一次。

| 场景 | 预期 | 结果 / 日志 |
| --- | --- | --- |
| 持续捏住 | 不重复提交 | 待填写 |
| 选中后手丢失 / 标签过期 | 取消预览，不运动 | 待填写 |
| 空载定位 | 磁铁始终抬起，不误拖其他棋子 | 待填写 |
| 正常拖动 | 蓝线来自实际遥测，释放后再验证 | 待填写 |
| 棋子滑脱 | 即使收到 COMPLETED 也不能成功 | 待填写 |
| 验证阶段标签丢失 | VERIFYING 超时，进入 ERROR | 待填写 |
| 断 TCP / 停 PC 主循环 | Pi 停止，不自动重放任务 | 待填写 |
| 关闭 PC 窗口 / 停 Pi 服务 | STOP/信号清理，检查实物停稳 | 待填写 |

完整协议、源码路径和恢复方法见 [技术说明书](TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)。填完记录并处理异常前，不能把离线测试结果写成实机验收通过。
