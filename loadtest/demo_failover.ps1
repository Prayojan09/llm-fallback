param(
    [string]$BaseUrl = "http://localhost:8000",
    [string]$ApiKey = $env:GATEWAY_API_KEY,
    [string]$AdminToken = $env:ADMIN_TOKEN,
    [int]$DurationSeconds = 120
)

$ErrorActionPreference = "Stop"
if (-not $ApiKey -or -not $AdminToken) {
    throw "Set GATEWAY_API_KEY and ADMIN_TOKEN in the environment."
}
$adminHeaders = @{ Authorization = "Bearer $AdminToken" }
$loadProcess = $null
try {
    Invoke-RestMethod -Method Put -Uri "$BaseUrl/admin/fake-provider/fake" `
        -Headers $adminHeaders -ContentType "application/json" -Body '{"available":true}' | Out-Null
    $env:BASE_URL = $BaseUrl
    $env:API_KEY = $ApiKey
    $env:MODEL = "demo-failover"
    $env:SCENARIO = "steady"
    $env:DURATION = "${DurationSeconds}s"
    $loadProcess = Start-Process -FilePath "k6" -ArgumentList @("run", "loadtest/k6/chat.js") -PassThru
    Start-Sleep -Seconds ([Math]::Max(10, [int]($DurationSeconds / 3)))
    Write-Host "Primary fake provider disabled. Watch X-Gateway-Provider and fallback metrics in Grafana."
    Invoke-RestMethod -Method Put -Uri "$BaseUrl/admin/fake-provider/fake" `
        -Headers $adminHeaders -ContentType "application/json" -Body '{"available":false}' | Out-Null
    Start-Sleep -Seconds ([Math]::Max(10, [int]($DurationSeconds / 3)))
    Write-Host "Primary restored; new requests should return to it after breaker recovery."
    Invoke-RestMethod -Method Put -Uri "$BaseUrl/admin/fake-provider/fake" `
        -Headers $adminHeaders -ContentType "application/json" -Body '{"available":true}' | Out-Null
    Wait-Process -Id $loadProcess.Id
} finally {
    Invoke-RestMethod -Method Put -Uri "$BaseUrl/admin/fake-provider/fake" `
        -Headers $adminHeaders -ContentType "application/json" -Body '{"available":true}' | Out-Null
    if ($loadProcess -and -not $loadProcess.HasExited) {
        Stop-Process -Id $loadProcess.Id
    }
}
