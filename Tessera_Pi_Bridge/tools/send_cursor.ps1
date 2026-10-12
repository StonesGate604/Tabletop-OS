param(
    [Parameter(Mandatory = $true)]
    [string]$Pi,

    [double]$X = 171.0,
    [double]$Y = 257.5,
    [double]$Seconds = 5.0,
    [ValidateSet("open", "grab", "none")]
    [string]$Gesture = "open",
    [int]$Port = 7421,
    [ValidateRange(1, 120)]
    [int]$RateHz = 30
)

$udp = [Net.Sockets.UdpClient]::new()
$sequence = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$count = [Math]::Max(1, [int][Math]::Ceiling($Seconds * $RateHz))
$delayMs = [Math]::Max(1, [int][Math]::Round(1000.0 / $RateHz))

try {
    1..$count | ForEach-Object {
        $sequence++
        $message = @{
            v = 1
            type = "cursor"
            seq = $sequence
            visible = $true
            x_mm = $X
            y_mm = $Y
            gesture = $Gesture
            confidence = 1.0
        } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($message)
        [void]$udp.Send($bytes, $bytes.Length, $Pi, $Port)
        Start-Sleep -Milliseconds $delayMs
    }

    1..3 | ForEach-Object {
        $sequence++
        $hidden = @{
            v = 1
            type = "cursor"
            seq = $sequence
            visible = $false
            gesture = "none"
        } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($hidden)
        [void]$udp.Send($bytes, $bytes.Length, $Pi, $Port)
        Start-Sleep -Milliseconds 20
    }
}
finally {
    $udp.Dispose()
}
