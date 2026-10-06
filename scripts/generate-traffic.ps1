param(
    [string]$BaseUrl = "http://localhost:8000",
    [ValidateRange(1, 3600)]
    [int]$DurationSeconds = 60,
    [ValidateRange(1, 100)]
    [int]$Concurrency = 5,
    [ValidateRange(0, 10000)]
    [int]$DelayMilliseconds = 50
)

$ErrorActionPreference = "Stop"
$endTime = [DateTime]::UtcNow.AddSeconds($DurationSeconds)
$target = "$($BaseUrl.TrimEnd('/'))/pay"

Write-Host "Sending payment traffic to $target for $DurationSeconds seconds with $Concurrency workers..."

$jobs = 1..$Concurrency | ForEach-Object {
    Start-Job -ArgumentList $target, $endTime, $DelayMilliseconds -ScriptBlock {
        param($Target, $EndTime, $DelayMilliseconds)

        $client = [System.Net.Http.HttpClient]::new()
        try {
            while ([DateTime]::UtcNow -lt $EndTime) {
                try {
                    $content = [System.Net.Http.StringContent]::new(
                        "{}",
                        [System.Text.Encoding]::UTF8,
                        "application/json"
                    )
                    $response = $client.PostAsync($Target, $content).GetAwaiter().GetResult()
                    [PSCustomObject]@{
                        Success = $response.IsSuccessStatusCode
                        StatusCode = [int]$response.StatusCode
                    }
                    $response.Dispose()
                    $content.Dispose()
                }
                catch {
                    [PSCustomObject]@{Success = $false; StatusCode = 0}
                }

                if ($DelayMilliseconds -gt 0) {
                    Start-Sleep -Milliseconds $DelayMilliseconds
                }
            }
        }
        finally {
            $client.Dispose()
        }
    }
}

try {
    $results = @($jobs | Wait-Job | Receive-Job)
}
finally {
    $jobs | Remove-Job -Force
}

$successful = @($results | Where-Object Success).Count
$failed = $results.Count - $successful
$serverErrors = @($results | Where-Object { $_.StatusCode -ge 500 }).Count

Write-Host "Traffic complete: total=$($results.Count), successful=$successful, failed=$failed, server_5xx=$serverErrors"

if ($results.Count -eq 0) {
    throw "No requests were sent. Check the service URL and local PowerShell job support."
}
