param(
    [Parameter(Mandatory = $true)][string]$Port,
    [Parameter(Mandatory = $true)][ValidateRange(0,180)][int]$Angle,
    [ValidateRange(0.5,8.0)][double]$HoldSeconds = 2,
    [switch]$MechanismClear
)

# Explicit one-angle test, never an automatic sweep. Stop Pi Bridge / Serial Monitor first.
if (-not $MechanismClear) {
    throw 'Remove the linkage or confirm safe mechanical clearance, then pass -MechanismClear.'
}
$serial = [IO.Ports.SerialPort]::new($Port, 115200, [IO.Ports.Parity]::None, 8, [IO.Ports.StopBits]::One)
$serial.NewLine = "`n"
$serial.ReadTimeout = 1000
$serial.WriteTimeout = 1000
try {
    $serial.Open()
    Start-Sleep -Milliseconds 2300
    $serial.DiscardInBuffer()
    $serial.WriteLine('IDENTIFY')
    $identity = $serial.ReadLine().Trim()
    if ($identity -ne 'READY TABLETOP_OS_LOWER 1') { throw "Unexpected firmware: $identity" }
    $serial.WriteLine('DISABLE')
    $disabled = $serial.ReadLine().Trim()
    if ($disabled -ne 'OK DISABLED') { throw "Cannot confirm motors disabled: $disabled" }
    $serial.WriteLine("SERVO $Angle")
    $response = $serial.ReadLine().Trim()
    if ($response -ne "OK SERVO $Angle") { throw "Servo rejected: $response" }
    Write-Output $response
    Start-Sleep -Milliseconds ([int]($HoldSeconds*1000))
    $serial.WriteLine('SERVO_OFF')
    Write-Output $serial.ReadLine().Trim()
    Write-Output 'Record measured clearance; edit local runtime config only after both safe angles are known.'
}
finally {
    if ($serial.IsOpen) {
        try { $serial.WriteLine('SERVO_OFF') } catch {}
        $serial.Close()
    }
    $serial.Dispose()
}
