# Tabletop OS PC Runtime V0.1

Python 3.11+。实现单棋子双捏合交互、PC RGB 合成、舵机升降永磁铁动作序列与观测验证。Pi 只显示最终像素并桥接设备。

## 先运行仿真

从仓库根目录运行；不会连接任何硬件：

```powershell
python -m venv pc-runtime/.venv
./pc-runtime/.venv/Scripts/python.exe -m pip install -e ./pc-runtime
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate
```

移动鼠标指向棋子，点击选中；松开后移动，第二次点击确认。Space 停止，R 恢复，H 回零。侧栏可模拟滑脱和标签丢失。仿真会独立维护执行器和棋子，电机完成后仍需观测验证。

无界面自动 Demo 和测试：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate --headless --demo --frame-output ./tmp/demo.ppm
./pc-runtime/.venv/Scripts/python.exe -m unittest discover -s pc-runtime/tests -v
```

自动 Demo 使用虚拟时间，成功应 completed_moves=1、motion_commands=2（空载定位+拖动）。仿真虚拟角度 60/120 不可当作实机标定。

## 摄像头和真实设备

```powershell
./pc-runtime/.venv/Scripts/python.exe -m pip install -e './pc-runtime[vision]'
./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/download_hand_model.py
./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/make_apriltag.py --id 0
./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/calibrate_camera.py --config ./pc-runtime/config/runtime.json
./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/check_vision.py --config ./pc-runtime/config/runtime.json --seconds 10
```

模型下载有固定 SHA256；运行时不自动下载。标签为 36h11/ID0，保留白边。相机四角按物理 (0,0)、(W,0)、(W,H)、(0,H) 点击，S 保存/R 重选/Esc 取消。换分辨率或机位后重标定。

复制 config/runtime.json 为 config/local-runtime.json，设置 Pi IP；安全角度实测后再填 gripper.release_angle/engage_angle、settle_s，最后 calibrated=true。目前用户角度未标定，默认锁住实际动作。Pi 的 allow_network_commands 也默认 false，必须完成硬件验证后人工启用。

```powershell
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --config ./pc-runtime/config/local-runtime.json --input camera --log ./pc-runtime/logs/hardware.log
```

真实模式要求摄像头观测，禁止用自动脚本或鼠标冒充真实棋子观测。HOME 会先抬起、等待再回零；双捏合动作按 release→seek→engage→drag→release→verify 执行。物理急停、角度、限位和相机误差仍待实物验收。

Linux 使用 .venv/bin/python；Tk 可能需要系统 python3-tk，本次未做 Linux GUI 验证。可编辑源码安装已验证，尚无独立桌面安装器。

详细架构和参数见[中文技术说明书](../docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)，明天实测按[联调记录表](../docs/HARDWARE_COMMISSIONING_ZH.md)。

## AprilTag 摄像头原型

该原型独立运行，使用 pupil-apriltags；正式 Runtime 的 vision.py 使用 OpenCV aruco + MediaPipe。原型的 stdout/NDJSON 目前没有接入 Runtime。requirements.txt 只用于原型，请使用单独虚拟环境，避免替换 Runtime 已锁定的视觉依赖。以下命令从仓库根目录开始；执行 cd 后，后续原型命令均在 pc-runtime 目录运行：

```powershell
cd pc-runtime
py -m venv .venv-apriltag
.\.venv-apriltag\Scripts\python.exe -m pip install -r requirements.txt
```

启动带预览的检测器：

```powershell
.\.venv-apriltag\Scripts\python.exe .\apriltag_camera.py --camera 1
```

默认每秒最多输出 10 次可读坐标：

```text
[14:03:27] ID 7  center_px=(951.42, 532.18)  heading=89.73 deg  margin=121.85
```

供后续 PC Runtime 进程读取时，改用逐行 JSON：

```powershell
.\.venv-apriltag\Scripts\python.exe .\apriltag_camera.py --camera 1 --output json
```

每一行都是一条 `vision.tags` 快照；`tags` 为空表示当前没有检测到标记。示例：

```json
{"v":1,"type":"vision.tags","seq":42,"captured_at_ms":1792087407000,"coordinate_space":"camera_px","camera":{"index":1,"width_px":1920,"height_px":1080},"tags":[{"tag_id":7,"center_px":{"x":951.42,"y":532.18},"heading_deg":89.73,"corners_px":[{"x":900.1,"y":480.2},{"x":1001.8,"y":480.0},{"x":1002.0,"y":584.3},{"x":900.0,"y":584.1}],"decision_margin":121.85,"hamming":0}]}
```

这里输出的是摄像头像素坐标，属于 `vision_observation`。在跨设备发送或驱动机构前，必须通过桌面四点标定/透视变换转换成统一的 `x_mm / y_mm`；不要直接发到 Pi 的 UDP 7421 诊断光标端口。

不打开预览窗口时可加 `--headless`，用 `Ctrl+C` 退出。物理 Insta360 Link 2 在本机通常是索引 `1`；若 Insta360 Controller 占用物理设备，可关闭 Controller，或临时使用虚拟摄像头索引 `6`。

生成测试标签并执行离线检测/叠加/NDJSON冒烟检查：

```powershell
.\.venv-apriltag\Scripts\python.exe .\smoke_test.py
```

原型默认相机索引为1；正式 Runtime 默认索引为0，可在本地 runtime 配置中修改，两者不会自动共享设置。
