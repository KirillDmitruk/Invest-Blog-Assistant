# Start/stop only the worker recorded by this project, never an arbitrary Python process.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$techRoot = Split-Path -Parent $PSScriptRoot
$techStateDir = Join-Path $techRoot 'data\tech-publisher'
$techStateFile = Join-Path $techStateDir 'worker.json'
$techAction = if ($args.Count) { [string]$args[0] } else { 'status' }

function Get-TechWorker {
    if (-not (Test-Path -LiteralPath $techStateFile)) { return $null }
    try {
        $techRecord = Get-Content -Raw -LiteralPath $techStateFile | ConvertFrom-Json
        $techProcess = Get-Process -Id ([int]$techRecord.processId) -ErrorAction Stop
        if ($techProcess.StartTime.ToUniversalTime().Ticks -ne [long]$techRecord.startTicks) { return $null }
        return $techProcess
    } catch { return $null }
}

$techWorker = Get-TechWorker
switch ($techAction) {
    'status' {
        if ($techWorker) { Write-Output "News publisher is running (PID $($techWorker.Id)). Logs: $techStateDir" }
        else { Write-Output 'News publisher is not running in the background. Start: .\hub news-start' }
        exit 0
    }
    'stop' {
        if ($techWorker) {
            & taskkill.exe /PID $techWorker.Id /T /F
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            Remove-Item -LiteralPath $techStateFile -ErrorAction SilentlyContinue
            Write-Output 'News publisher stopped. News and delivery history are preserved.'
        } else { Write-Output 'No background News publisher to stop.' }
        exit 0
    }
    'start' {
        if ($techWorker) { Write-Output "Already running (PID $($techWorker.Id))."; exit 0 }
        Push-Location -LiteralPath $techRoot
        try {
            $techReadyOutput = & uv run --quiet --extra translation python tools/check_tech_publisher.py
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
            $techReady = ($techReadyOutput -join "`n") | ConvertFrom-Json
            New-Item -ItemType Directory -Path $techStateDir -Force | Out-Null
            $techStdout = Join-Path $techStateDir 'output.log'
            $techStderr = Join-Path $techStateDir 'errors.log'
            $techStarted = Start-Process -FilePath $techReady.python -ArgumentList @('-m','invest_blog.cli','tech-publisher') `
                -WorkingDirectory $techRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $techStdout -RedirectStandardError $techStderr
            $techTicks = $techStarted.StartTime.ToUniversalTime().Ticks
            @{processId=$techStarted.Id; startTicks=$techTicks; channel=$techReady.channel; nextAt=$techReady.next_at} |
                ConvertTo-Json | Set-Content -LiteralPath $techStateFile -Encoding UTF8
            Start-Sleep -Seconds 2
            if (-not (Get-TechWorker)) { throw "News publisher exited. See $techStderr" }
            Write-Output "News publisher started for $($techReady.channel). Next slot: $($techReady.next_at)"
            Write-Output 'It runs while this computer is awake. Status: .\hub news-status; stop: .\hub news-stop'
        } finally { Pop-Location }
        exit 0
    }
    default { throw 'Use start, status or stop.' }
}
