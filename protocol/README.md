# Tabletop OS 通信协议 v1

## 实际通道

| 通道 | 默认配置 | 用途 |
| --- | --- | --- |
| TCP 7420 NDJSON | network.control_port | 心跳、状态、显示会话、单次运动与舵机命令 |
| UDP 7422 TOSF | network.frame_port | PC 最终 64×96 RGB888 帧；CRC32、会话、乱序组帧与过期丢弃 |
| UDP 7421 JSON | network.cursor_port | 保留的光标诊断；只在 display.mode=diagnostic 绘制 |
| USB Serial 115200 8N1 | serial.baud/device | Pi 与正式 Arduino 固件的 ASCII 命令/事件 |

[控制请求 schema](control-v1.schema.json)；[TOSF 帧布局](frame-v1.json)。默认 display.mode=frames，不再由 Pi 合成游戏图层。

## TCP 请求

每行一个 UTF-8 JSON，以 LF 结束，兼容 CRLF，默认最多 4096 字节。只有一个控制客户端；只有合法 heartbeat 续租，默认 1500 ms 过期。断线或过期会清显示会话并停止机构。

```json
{"v":1,"type":"heartbeat","request_id":"hb"}
{"v":1,"type":"display.open","request_id":"display","session":123}
{"v":1,"type":"status.get","request_id":"status"}
{"v":1,"type":"motion.home","request_id":"home"}
{"v":1,"type":"motion.move_to","request_id":"move","x_mm":20,"y_mm":20,"speed_mm_s":2}
{"v":1,"type":"actuator.servo","request_id":"lift","angle":70}
{"v":1,"type":"motion.stop","request_id":"stop"}
{"v":1,"type":"actuator.servo_off","request_id":"off"}
```

角度仅为格式示例。HOME/MOVE/SERVO 需要 request_id、CONFIG LAST_TASK_ID 同步和运动联锁；最近64个已接受ID拒绝重复，不是持久结果缓存。运动默认关闭；MOVE还需回零。actuator.completed 表示固件接受舵机目标，不代表机械到位。

## TOSF

先通过有效租约的 TCP display.open 绑定非零 uint32 session，收到 display.opened 后再发数据。26字节大端头 `!4sBBHHIIHHI`，随后最多1200字节像素；一帧18432字节、16块，末块432字节。完整帧 CRC32 同 zlib.crc32。默认100ms组帧、250ms无新完整帧清屏。旧帧不重传，断线清会话，非法/不完整帧不能显示。session不是鉴权。

Python发送：pc-runtime/src/tabletop_os/frame_protocol.py；C++接收：pi-bridge/src/frame_protocol.cpp、frame_receiver.cpp。

## Arduino

正式身份 `READY TABLETOP_OS_LOWER 1`；Pi也接受旧身份TESSERA_LOWER 1，但MOVE要求新版CONFIG的LAST_TASK_ID字段。串口打开先STOP/SERVO_OFF，再IDENTIFY、PING、CONFIG、WATCHDOG ON、STATUS、TELEMETRY ON。完整命令表在[固件说明](../firmware/arduino/TabletopOS_Lower_Controller/README_中文.md)。

真实事件是 ACK/STARTED/COMPLETED/ERROR，没有 RUNNING/DONE。运行模式看 STATE 的 TASK_MOVE/HOMING/TRACKING/STOPPING。COMPLETED只表示执行器脉冲规划完成，棋子到达由PC的AprilTag观测另行验证。

超时、恢复、字节布局与完整时序见[中文技术说明书](../docs/TABLETOP_OS_TECHNICAL_GUIDE_ZH.md)。
