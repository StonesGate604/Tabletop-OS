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

## 从电脑手动控制 Pi / Arduino

`tools/control_pi.py` 只使用 Python 标准库，无需相机、视觉依赖或安装 Runtime。先将磁铁抬起并固定，验证限位和方向；该工具不会自动控制升降舵机，不属于棋子观测验证流程。Pi 仍须人工设置 `motion.allow_network_commands=true` 并重启服务；工具不会修改 Pi 配置。

在电脑项目根目录的 PowerShell 中执行（IP 换成实际树莓派地址）：

```powershell
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 status
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 home
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 move --x 10 --y 10 --speed 2
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 move --x 20 --y 10 --speed 2
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 stop
```

坐标为绝对毫米坐标，速度为 mm/s。每条等完成再执行下一条；未回零会拒绝移动。HOME/MOVE 在同一 TCP 连接中持续发心跳并等待匹配的完成事件，不自动重连或重发动作；退出时关闭连接会触发 Pi 的 STOP，但不重启服务，正常情况下可保留坐标参考。断电、Arduino 复位或位置参考失效后必须重新回零。

运动中在执行命令的窗口按 Ctrl+C，工具会请求 STOP 并等待 Arduino 的停止确认。Pi 只允许一个控制连接，另一个窗口的 `stop` 无法抢占正在运行的连接，也不要同时运行 Runtime 或树莓派本地回零脚本。`stop` 的发送 ACK 不等于实物停止；工具等待 `motion.stopped`。遇到机械危险仍使用物理急停。Ctrl+C 清理和退出也无法保证断电/进程被强杀时的软件停止确认。

本地回环测试 `python -m unittest discover -s pc-runtime/tests -p test_control_pi.py -v` 覆盖分片回复、持续心跳、未回零拒绝、低速参数、错误/超时后 STOP 和等待停止确认；不驱动硬件。

### 第一块 64×32 灯板与手动运动联动

两块面板仍串联，保留 Pi 的 64×64 逻辑画布及已校准的面板映射；启用 deployed 配置的 `display.enabled=true`、`display.mode=frames`，首次可将亮度设为 15。重启 Pi Bridge 可能复位 Arduino，此后先查询状态并按需重新回零。默认映射下，工具只画第一块对应的上 32 行，下 32 行始终为黑；需要通过实物数字 1 和左右角标核对屏幕顺序、方向。

```powershell
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 panel-test --seconds 10
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-first home
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-first move --x 10 --y 10 --speed 2
```

`panel-test` 不发送运动命令，画面为大数字 1、左上绿色块和右上蓝色块。`--display-first` 使用同一个控制连接建立显示会话、维持心跳，并通过 UDP 7422 发送 TOSF 最终帧；Pi 状态确认接收到至少一帧后才发送动作。它不能判断实体灯板是否真正亮起，仍需现场观察。测试结束或连接关闭后自动清屏，不要另开一个进程占用显示/运动连接。

回零时底部显示蓝色状态条，因坐标尚无参考，不显示有效位置标记。回零后，青色标记为步进脉冲估计的机构位置，绿色十字为移动目标，蓝线为本次会话记录的机构轨迹；底部绿色表示已回零且空闲。遥测过期后隐藏位置标记并显示蓝色叉。图像是把 342×515 mm 行程压缩到第一块面板内的缩略诊断图，不是棋子真实观测，也不代表灯板像素已与桌面毫米位置对齐。红色通道仍关闭。

本机测试共 12 项，新增覆盖 TOSF/UDP 组帧、显示会话在运动前建立、无运动命令的亮屏测试、第二块区域及红色全黑、坐标裁剪。2026-10-23 从电脑对真实 Pi 执行过 2 秒 `panel-test`，状态确认收到图像帧；未据此宣称灯板物理输出通过。

### 两块灯板组成完整 64×64 显示

在电脑命令中使用 `--display-all`，位置、目标和轨迹将使用两块拼接后的完整画布；状态条在旋转前位于第 60 行（从 0 开始），靠近逻辑整屏底部，不再位于两块交界处。Pi 的两块串联及 64×64 映射继续沿用，仅需更新电脑端工具。

```powershell
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-all panel-test --seconds 10
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-all home
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-all move --x 20 --y 20 --speed 2
```

整屏测试上半显示数字 1、下半显示数字 2，底部带蓝条，不发送运动命令；原 `panel-test` 默认和 `--display-first` 仍保留单块模式，两个显示选项互斥。整屏模式下坐标缩略图纵向覆盖两块面板，仍需进行毫米到像素的实物标定才能用于桌面位置对齐。测试共 14 项，新增验证整屏状态条位置和两块显示区域的 UDP 图像数据。

按当前装配方向，整屏模式现在默认将整幅画面在屏幕平面内绕 Z 轴旋转 180°：输出像素为 `(63-x, 63-y)`，数字、位置、轨迹、角标及状态条整体旋转，两块内容随之交换位置，颜色通道不变。上述上/下及底部描述是旋转前的逻辑布局；旋转后状态条输出在第 3 行。运动请求的毫米坐标、轴方向及 Arduino 配置保持不变。用 `--rotation 0` 可临时恢复原显示方向；单块模式默认不旋转，显式选择 180 时只旋转第一块内部，不点亮第二块。

```powershell
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-all --rotation 180 panel-test --seconds 10
python ./pc-runtime/tools/control_pi.py --host 192.168.1.2 --display-all --rotation 0 panel-test --seconds 10
```

测试现共 17 项，包含 RGB 像素整体旋转、单块旋转范围及旋转显示时运动坐标保持不变。

按用户确认，以蓝色状态边为下方观察时，机构回零点位于左下角，X 正方向向右、Y 正方向向上。因此缩略图的坐标映射使用左下原点：完整画布在整体旋转前将 `(0,0)` 映射到 `(2,56)`，`(342,515)` 映射到 `(61,2)`，再统一应用当前的 180° 显示旋转。位置、目标和轨迹共用该映射；不修改 Arduino 电机方向或步数换算。当前 18 项测试包含原点及两个正方向的映射回归测试。

手动 `move` 使用绝对毫米坐标，`--speed 2` 为 2 mm/s。回零后约 `(2,2)` 到 `(20,20)` 每轴只变化约 18 mm；再次发送相同目标不会继续前进。工具在发送 MOVE 前会打印机构报告的起点、绝对目标、X/Y 差值及速度。报告坐标仍是步进脉冲估计，实际距离需用尺子标定，不能仅凭 COMPLETED 判断步距正确。

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

### 虚拟摄像头与白板裁切

正式 Runtime 和四点标定工具共用 `camera.crop_x/crop_y/crop_width/crop_height`，单位为原始相机像素；宽、高均为 0 时保留全图。例如 1280×720 输入可配置 `index=6, crop_x=150, crop_y=40, crop_width=1070, crop_height=640`，但设备索引和区域需按本机画面确认。虚拟相机启动时会等待有效图像，避免使用初始黑帧。

标定窗口显示裁切后的图像，手势与 AprilTag 也在同一区域检测，预览保持该区域的宽高比。修改裁切位置、尺寸、相机分辨率或 Insta360 的缩放/翻转/白板校正后，必须重新四点标定；裁切边界不等于已确认的机械行程边界。`--image` 输入也应是未裁切的相机原图。

### 仅测试手势指针（灯板区域独立映射）

运行 `./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/hand_pointer.py --config pc-runtime/config/local-runtime.json`。窗口内按画面中的左上、右上、右下、左下，点击两块灯板合成屏幕在桌面上的实际覆盖四角，按 S 开始。第一点后画面暂停供选区；启动后恢复实时识别。R 清除选区，Esc 或关闭窗口退出。每次启动重新选区，最近结果保存为独立的 `config/local-display-map.json`，不替代机械毫米坐标标定。

指针取拇指尖和食指尖的中点；超出选区或没有检测到手时隐藏手指光圈，已记住的标签方框仍保留。默认沿用整屏 180° 输出旋转，可用 `--rotation 0` 比较实体方向。只运行此工具，避免其他程序占用 Pi 的唯一控制连接。默认预览模式发送心跳、状态和显示帧，不提交回零、移动或舵机动作；底层连接退出时仍发送停止请求。灯板小于机械行程时，仅覆盖灯板区域，不把整个机械行程压缩到灯板上。必须现场核对指针四角与移动方向，确认 Pi 收帧不等于灯板物理对齐通过。

仅 X 方向反向时，使用 `--reuse-map --flip-x`；它将映射的 X 分量镜像，Y 保持原值。`--no-flip-x` 关闭镜像；镜像和旋转设置保存在本机 `config/local-pointer-view.json`，以后启动自动沿用。该设置只修正手势指针的显示方向，不改变机械坐标或电机方向。

工具现在同时识别 AprilTag 36h11 的所有 ID：标签位置显示绿色正方形，手势显示青色光圈，摄像头预览标注 ID。先张开拇指与食指，在标签附近捏合选中，松开后移动手预览蓝色目标和连线，再捏合冻结目标预览；按 C 取消后可再次选择。确认只改变预览，不生成运动任务。选取距离为 6 个灯板像素；手离开时取消选择。

标签被手遮挡时，默认一直保持最后观测位置和原有方框亮度；摄像头预览用 `HOLD` 标注缓存位置，不会因标签遮挡而取消选择。缓存不随手移动，不延长自身时间戳；再次看到同一 ID 后立即使用新位置。手离开时取消选择，但记住的标签位置仍保留。重设选区或退出程序会清除位置缓存。默认 `--hold-seconds 0` 表示一直保持；可用 `--hold-seconds 5` 改成超时 5 秒后隐藏并取消选择。此机制仅用于灯光预览；正式机械 Runtime 仍要求新鲜观测，未扩大其动作或到位验证超时。

两张相同 ID 的标签仍同时显示，但该 ID 在本次测试中禁止选择，避免遮挡后误认另一张。更换为不同 ID 后重新启动；可用 `python pc-runtime/tools/make_apriltag.py --id 1 --output pc-runtime/outputs/apriltag-1.png` 生成第二张标签，打印时保留白边。手和标签共用映射，并统一应用已确认的 X 镜像与旋转。

### 持续吸合的拖动测试

磁铁已由机构保持吸合时，可暂不标定升降舵机，用独立模式测试已吸住棋子的短距离拖动。先拿开棋子、清空回零路径并完成回零，再把机构定位到测试位置，棋子放在磁铁正上方；该模式不会自动前往别的棋子，也不发送升降或回零命令。

```powershell
./pc-runtime/.venv/Scripts/python.exe pc-runtime/tools/hand_pointer.py --config pc-runtime/config/local-runtime.json --reuse-map --constant-engage
```

灯板选区与机械毫米标定各自独立：目标经相机坐标转换到 `local-calibration.json` 的机械坐标，显示镜像/旋转不参与电机坐标转换。先核对相机毫米标定；如果磁铁位置与选中标签不符（默认容差 3 mm）、尚未回零、遥测过期、标签当前不可见或 Pi 未启用网络运动，确认会显示原因并拒绝动作。标签无限记忆仍只用于显示，不用于替代动作开始和完成后的真实观测。

先张开手，在已吸住的棋子附近捏合选中，松开并移动手，再捏合提交一次运动。默认速度 2 mm/s，默认不额外限制单次距离，目标仍须在机械工作区内；运动超时按路程和速度留足时间。可用 `--speed` 调整速度，`--max-drag-mm 20` 恢复单次距离限制（默认 0 为不额外限制）。完成电机任务后还需至少三帧新的标签观测确认棋子到位，随后显示 DONE。按 C 清除结果以开始下一次；动作中 C 或 Space 请求停止。发生失联/故障/超时后不自动重放动作。退出沿用原有停止处理，Pi 的安全停止仍可能执行 `SERVO_OFF`，因此本模式不保证舵机一直通电保持位置。

现场测试可显式添加 `--allow-pickup-offset` 暂时跳过棋子与机构位置对齐检查。该选项仅作用于本次进程，不写入本地配置；不带该选项启动时恢复检查。机构将直接前往目标，不自动先去标签下方；工作区、回零、实时观测、停止与到位验证继续生效。
