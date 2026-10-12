param(
    [Parameter(Mandatory = $true)]
    [string]$Pi,

    [ValidateSet("ping", "status", "heartbeat", "stop")]
    [string]$Command = "ping",

    [int]$Port = 7420,
    [int]$TimeoutMs = 2500
)

$type = switch ($Command) {
    "status" { "status.get" }
    "heartbeat" { "heartbeat" }
    "stop" { "motion.stop" }
    default { "ping" }
}
$requestId = "manual-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
$payload = @{
    v = 1
    type = $type
    request_id = $requestId
} | ConvertTo-Json -Compress

$client = [Net.Sockets.TcpClient]::new()
try {
    $connect = $client.ConnectAsync($Pi, $Port)
    if (-not $connect.Wait($TimeoutMs)) {
        throw "Timed out connecting to ${Pi}:${Port}"
    }

    $stream = $client.GetStream()
    $stream.ReadTimeout = $TimeoutMs
    $utf8 = [Text.UTF8Encoding]::new($false)
    $writer = [IO.StreamWriter]::new($stream, $utf8, 1024, $true)
    $reader = [IO.StreamReader]::new($stream, $utf8, $false, 1024, $true)
    $writer.AutoFlush = $true
    $writer.WriteLine($payload)
    $response = $reader.ReadLine()
    if ($null -eq $response) { throw "The bridge closed the connection without a response" }
    $response
}
finally {
    $client.Dispose()
}
