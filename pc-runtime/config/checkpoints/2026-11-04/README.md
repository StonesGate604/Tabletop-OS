# Patrol 棋盘进度快照：2026-11-04

本目录保存本次调试结束时的本机配置，供恢复进度使用。日常运行仍使用 `pc-runtime/config/local-*.json`，后续修改不会自动更新本快照。

- 玩家：Tag 0，Family 为 tag36h11。
- 守卫：虚拟守卫，以红色格子显示并按回合巡逻。
- 地图：8 列 × 12 行，320 × 480 mm，每格 40 × 40 mm。
- 玩家起点 `(11,0)`，目标 `(11,4)`，出口 `(11,7)`。
- 手部接近范围：进入 80 mm、退出 120 mm；标签最后位置保留 5 秒。
- 相机：本机索引 6、1280 × 720；显示旋转 0°，X 镜像启用。
- 用户已确认玩家识别与接近预览可用。树莓派 RGB Bridge 已由用户完成编译部署；完整游戏流程和机械运动仍需单独验收。

包含 Runtime、Patrol、灯板映射、显示方向及已有机械标定五份 JSON。保留机械标定文件不代表重新验证了机械精度。机位、棋盘或相机画面变动后应重新标定；相机索引和树莓派地址也需按设备调整。

保存前验证：完整 PC 软件测试 `Ran 219 tests ... OK`（34.224 秒）；本机 Patrol 配置校验通过；五份 JSON 快照与当前本机文件的 SHA256 一致。通信测试使用本机模拟服务，没有连接或操作实物设备。

需要恢复时，先关闭 Patrol，在仓库根目录执行下列命令。这会覆盖当前五份本机配置；如需保留后续调整，请先另行备份。

```powershell
$patrolSnapshot = '.\pc-runtime\config\checkpoints\2026-11-04'
@('local-runtime.json', 'local-patrol.json', 'local-display-map.json', 'local-pointer-view.json', 'local-calibration.json') | ForEach-Object {
    Copy-Item -LiteralPath (Join-Path $patrolSnapshot $_) -Destination (Join-Path '.\pc-runtime\config' $_) -Force
}
```

恢复后从仓库根目录启动：

```powershell
.\pc-runtime\.venv\Scripts\python.exe -m tabletop_os --app patrol --config .\pc-runtime\config\local-runtime.json --patrol-config .\pc-runtime\config\local-patrol.json --led
```

先将 Tag 0 放到蓝色起点并无遮挡，等 `Phase: PLAYER_TURN` 后再靠近预览。该命令启用 LED，不启用机械运动。Python 环境、MediaPipe 模型及树莓派已安装的程序不在配置快照内。
