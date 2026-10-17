# Tabletop OS V0.1 软件验证记录（2026-10-16）

本记录针对当前工作区的软件候选版。没有上传固件，没有连接摄像头、Pi、HUB75、电机或舵机做实测。用户确认机构为舵机升降永磁铁，角度尚未标定；PC calibrated=false、Pi allow_network_commands=false 均保留。

## 环境

- Windows NT 10.0.26300 x64，PowerShell 7.6.5，Python 3.11.9，Tk 8.6。
- PC 专用 .venv：MediaPipe 0.10.35、OpenCV contrib 4.14.0.94、NumPy 2.4.6。
- 主机 C++：便携 w64devkit/GCC 16.2.0，CMake 3.31.10、Ninja 1.13.2、Boost 1.87.0、nlohmann/json 3.12.0；下载/构建依赖在被忽略的 tmp 下，没有系统安装器改动。
- Arduino CLI 1.5.1、Arduino AVR Core 1.8.7、Servo 1.3.0。
- HUB75 官方头文件 checkout：`14464b3ad8974665f75a3c82355cf872311fec21`，只做硬件renderer语法编译，未链接/运行Pi硬件库。

## 已执行的结果

| 验证 | 结果 | 能证明什么 / 不能证明什么 |
| --- | --- | --- |
| Python unittest discover | 28项通过 | 双捏合、去抖、陈旧输入不重复触发、独立位置验证、超时、停止、HOME先释放、旧服务执行锁、配置、帧和绘制；不模拟真实磁力/摩擦 |
| 真实MediaPipe模型 | 加载并对空白图VIDEO推理通过 | API/模型可用，空白图无手；没有真实手检测准确率和现场帧率 |
| 真实OpenCV AprilTag | 生成36h11/ID0后离线检出并映射中心通过 | 检测API与平面变换互通；没有遮挡/反光/标签高度实测 |
| C++ CTest | 2个可执行测试通过 | FrameAssembler乱序/CRC/超时/回绕；正式.ino配假HAL的运动、重复ID、回零、原始限位、急停、看门狗、跟随超时、servo超时与输入恢复 |
| C++真实进程黑盒测试 | 7项通过 | TCP分帧/多行/坏JSON/超长、单控制者、心跳租约、Python帧跨C++解码、过期清会话、真实PC网络线程、主循环停滞不续命；配置禁用serial/GPIO |
| Pi Bridge主机编译 | HUB75=OFF成功 | C++17源码与Windows socket链接可构建；不是Pi目标链接或USB集成测试 |
| HUB75 renderer | 对官方真实头文件 `g++ -fsyntax-only` 通过 | API/类型匹配；不是灯板双缓冲或刷新验收 |
| Uno目标编译 | 成功，17310/32256字节Flash（53%）、643/2048字节全局SRAM（31%） | 新版固件能为Uno构建；不证明脉冲精度、负载和温升 |
| 自动完整仿真 | 一次动作成功 | 最终piece=(140,160)mm等于target，2条MOVE（seek+drag），46个峰值拖影点，虚拟耗时11.617秒；不是实际墙钟速度测量 |
| PC与Pi配置检查 | 均通过 | 参数可解析；不证明设备路径、角度或相机有效 |
| Tk界面冒烟 | 隐藏窗口初始化、RGB预览更新、关闭通过 | 只检查控件和生命周期，不代表人工交互与画面布局验收 |
| 脚本/文档语法 | 安装Bash和说明书Bash块通过bash -n；3个PowerShell工具及7个说明书块通过Parser | 不执行部署、串口标定和上传；System.IO.Ports类型可用但未打开端口 |

重跑入口：

```powershell
./pc-runtime/.venv/Scripts/python.exe -m unittest discover -s pc-runtime/tests -v
ctest --test-dir pi-bridge/build-host -C Release --output-on-failure
./pc-runtime/.venv/Scripts/python.exe pi-bridge/tests/test_bridge.py --bridge pi-bridge/build-host/tabletop-os-bridge.exe -v
./pc-runtime/.venv/Scripts/python.exe -m tabletop_os --simulate --headless --demo --frame-output ./tmp/demo.ppm
python tools/validate_technical_guide.py
```

上述C++路径是本次Ninja产物；VS生成器可能在Release子目录。文档校验包含代码指纹、文件链接、关键符号和示例JSON，不会验证自然语言真伪或Mermaid渲染。

说明书已绑定63个代码/配置/测试文件的SHA256（计算前将CRLF换行统一为LF），静态检查66个本地链接、10处显式类/函数引用及9个Mermaid代码块。哈希只能发现代码变化，变更后仍需人工重新解释和审阅文字。

推送前合并远程提交894145c，保留独立AprilTag原型apriltag_camera.py、requirements.txt及smoke_test.py。两个Python脚本通过py_compile；本次未重新安装pupil-apriltags或运行原型的smoke_test，已有28项测试针对正式Runtime。原型使用单独.venv-apriltag环境，其stdout输出尚未接入Runtime。已按Git索引导出复查全部源码指纹，避免检出换行差异造成误报。

## 实机待验收

舵机两个安全角度、抬起间距/稳定时间、PWM关闭后机构行为；相机机位/分辨率/四角与独立点误差、真实手与标签遮挡；步距/方向/限位/完整回零/带负载速度/制动；串口复位与USB拔插；Pi硬件权限/HUB75 Remap/红色故障/帧率与视觉延迟；磁铁空载定位不误吸与终点验证；关闭PC/服务时实物停稳。

按 [实机联调记录表](HARDWARE_COMMISSIONING_ZH.md)填写结果后，再更新 [技术说明书](TABLETOP_OS_TECHNICAL_GUIDE_ZH.md) 的验收状态。历史实验固件成功不等于本候选版已经完成实物交付。
