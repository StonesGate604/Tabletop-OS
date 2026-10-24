# PC 主程序与系统/应用分层

## 一个启动入口

从仓库根目录启动 PC 主程序：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os
```

默认打开手势拖动测试应用的预览模式，使用本机 `local-runtime.json`（存在时）。只显示画面，不自动启用运动。已有选区且相机机位未变化时，添加 `--reuse-map`。

复现现场的 15 mm/s 持续吸合联动设置：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --app gesture-drag --reuse-map --constant-engage --allow-pickup-offset --speed 15
```

`--allow-pickup-offset` 只对本次进程跳过位置对齐检查；不带它启动即可恢复检查。系统仍检查真实标签观测、回零、遥测、工作区及动作完成后的独立观测。不会自动回零、接近棋子或重放旧动作。Space 请求停止，C 取消/重置，R 重选灯板区域，Esc 退出。

原单棋子应用和仿真仍从同一入口选择：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate --headless --demo
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --app single-piece --input camera --config ./pc-runtime/config/local-runtime.json
```

最后一条是带舵机升降的原流程，仍要求完成安全角度标定。`--simulate` 自动选择单棋子应用，保持原命令兼容。

`tools/hand_pointer.py` 现在只是兼容启动脚本，内部调用统一入口的 `gesture-drag` 应用，不再维护另一套相机/电机主循环。

## 职责划分

| 部分 | 位置 | 职责 |
| --- | --- | --- |
| 主入口 | `pc-runtime/src/tabletop_os/cli.py` | 选择应用、加载配置、日志和启动；默认不会开启实物运动 |
| 系统服务入口 | `system/session.py`：`TabletopSystem` | 独占设备连接、读取事件、执行动作检查、坐标转换、显示发送和停止处理 |
| 相机服务 | `system/camera.py` | 采集、裁切、手势识别、同一帧分别生成灯板及机械观测 |
| 坐标与标签记忆 | `system/coordinates.py`、`system/tracking.py` | 统一坐标转换；保存最后观测位置，不伪造新鲜观测 |
| 显示服务 | `system/display.py` | 根据应用场景绘制方框、光圈和目标；统一应用灯板镜像/旋转 |
| 手势测试应用 | `apps/gesture_drag.py` | 双捏合选择、目标预览、确认一次移动意图、生成场景 |
| 单棋子应用 | `apps/single_piece.py` | 原单棋子选择、规则状态与结果验证状态机；原 `Runtime` 继续作为其宿主 |
| 操作窗口 | `gesture_ui.py` | 选区、按键、摄像头和状态预览；经应用/系统接口操作，不直接发送电机命令 |

相机、电机、串口及网络适配器等已有底层组件继续复用。`interaction.py`、`preview.py` 保留旧导入兼容，实际应用状态机已移到 `apps/`。

## 应用如何使用系统

每帧顺序为：相机服务产生输入 → 系统更新标签记忆和设备状态 → 应用更新交互并提出意图 → 系统检查、执行、合成显示。

应用只提出“把选中 ID 移到这个灯板区域目标”的请求，并提供 `Scene`（光标、标签、选中与目标）。它不持有摄像头、网络套接字或电机驱动，也不负责把灯板像素换算成毫米。系统独立持有运动任务，即使手离开导致应用清除选择，正在执行的目标也不会丢失。

以后增加游戏时，游戏应用负责回合、合法走法和胜负判断，再调用同一组系统服务。游戏不得把设备收到完成回报直接解释为棋子到位；必须使用系统独立验证后的结果。

## 保留的行为

- Insta360 虚拟相机、白板裁切、独立灯板选区与机械标定。
- 已确认的 X 镜像、180° 显示旋转、绿色方框、青色手指光圈。
- 遮挡后一直保持标签位置和亮度，直到下一次真实观测更新。
- 缓存用于显示和交互预览；启动运动与验证结果仍使用实时机械观测。
- 持续吸合测试及可选的位置偏差覆盖；正式升降流程不受该测试选项影响。

摄像头与 UI 的回归测试使用模拟对象，不驱动真实设备。当前双映射相机服务支持平面四点标定；带镜头畸变参数的标定会被明确拒绝，需要先建立一致的镜头校正流程后再接入。

## 本次验证

系统/应用拆分完成后，120 项自动测试通过，包含原有行为、应用切换、摄像头模拟、窗口重选区/退出清理、单次移动提交及停止处理。原 `--simulate --headless --demo` 完整演示也通过，并验证了未加载视觉依赖时仍可运行仿真。实机联调基线见 [2026-10-24 记录](VALIDATION_2026-10-24.md)；本次结构调整未自动发起实物运动。
