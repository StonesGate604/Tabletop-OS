# Tabletop OS PC Runtime

视觉输入的第一个可运行原型已经加入：`apriltag_camera.py` 可从 Windows 摄像头识别 `tag36h11`，显示检测框、ID、中心像素坐标和二维朝向，并把观测打印为文本或 NDJSON。

PC Runtime 将负责：

- 摄像头输入、桌面标定与手势识别；
- 棋子选择、游戏状态和规则判断；
- 光标、移动轨迹、攻击特效及其他动画；
- 合成最终 64×64 画面并发送给 Pi；
- 发送可靠运动命令并读取机构的实际状态。

Pi 只接收最终像素结果并驱动硬件，不保存游戏特效逻辑。

## AprilTag 摄像头原型

首次运行先创建隔离环境：

```powershell
cd pc-runtime
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动带预览的检测器：

```powershell
.\.venv\Scripts\python.exe .\apriltag_camera.py --camera 1
```

默认每秒最多输出 10 次可读坐标：

```text
[14:03:27] ID 7  center_px=(951.42, 532.18)  heading=89.73 deg  margin=121.85
```

供后续 PC Runtime 进程读取时，改用逐行 JSON：

```powershell
.\.venv\Scripts\python.exe .\apriltag_camera.py --camera 1 --output json
```

每一行都是一条 `vision.tags` 快照；`tags` 为空表示当前没有检测到标记。示例：

```json
{"v":1,"type":"vision.tags","seq":42,"captured_at_ms":1792087407000,"coordinate_space":"camera_px","camera":{"index":1,"width_px":1920,"height_px":1080},"tags":[{"tag_id":7,"center_px":{"x":951.42,"y":532.18},"heading_deg":89.73,"corners_px":[{"x":900.1,"y":480.2},{"x":1001.8,"y":480.0},{"x":1002.0,"y":584.3},{"x":900.0,"y":584.1}],"decision_margin":121.85,"hamming":0}]}
```

这里输出的是摄像头像素坐标，属于 `vision_observation`。在跨设备发送或驱动机构前，必须通过桌面四点标定/透视变换转换成统一的 `x_mm / y_mm`；不要直接发到 Pi 的 UDP 7421 诊断光标端口。

不打开预览窗口时可加 `--headless`，用 `Ctrl+C` 退出。物理 Insta360 Link 2 在本机通常是索引 `1`；若 Insta360 Controller 占用物理设备，可关闭 Controller，或临时使用虚拟摄像头索引 `6`。
