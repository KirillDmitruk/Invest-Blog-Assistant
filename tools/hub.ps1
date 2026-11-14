# Windows entry point; arguments after the action are passed through unchanged.
$ErrorActionPreference = 'Stop'
# Keep Russian CLI output readable in Windows terminals and redirected logs.
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$hubArguments = @($args)
$hubExtras = @()
if ($hubArguments.Count -gt 0 -and $hubArguments[0] -eq '--translation') {
    $hubExtras = @('--extra', 'translation')
    $hubArguments = @($hubArguments | Select-Object -Skip 1)
}
$hubAction = if ($hubArguments.Count) { [string]$hubArguments[0] } else { 'help' }
$hubForward = @($hubArguments | Select-Object -Skip 1)

if ($hubAction -in @('help', '--help', '-h')) {
    Write-Output @'
Content Hub - Windows commands

  .\hub web [--port 8080]      Start the local dashboard
  .\hub bot                    Start the Telegram bot and scheduler
  .\hub --translation tech-publisher  Run only Tech Telegram news
  .\hub translation-model      Install the local EN -> RU model once
  .\hub news-start             Start Tech news in the background
  .\hub news-status            Check the background publisher
  .\hub news-stop              Stop the background publisher
  .\hub trade-start            Start the PAPER futures worker
  .\hub grok-key               Create a local connector credential (no paid API)
  .\hub grok-connector         Run isolated MCP on localhost:8769
  .\hub trade-status           Check the PAPER worker
  .\hub trade-stop             Stop the PAPER worker
  .\hub install                Install application and test dependencies
  .\hub test [-k name]          Run tests
  .\hub backup [--db PATH]      Back up SQLite safely
  .\hub accounts               List brokerage accounts
  .\hub sync [--days 30]        Sync portfolio data
  .\hub tech-news              Collect RSS news
  .\hub placements             Check upcoming bond placements
  .\hub weekly                 Preview a weekly post (also monthly/plan/paper/glossary/purchase)
  .\hub preview weekly         Full CLI syntax is also supported
  .\hub diagnose --start YYYY-MM-DD --end YYYY-MM-DD
  .\hub audit | bonds | risk | performance | iis-review
  .\hub backfill --days 365 --partial

All other CLI actions and their arguments are forwarded as supplied.
Use .\hub ACTION --help for CLI options.
Optional local translation: .\hub --translation install --python 3.13
Then use .\hub --translation web or .\hub --translation bot.
Stop a running web server or bot with Ctrl+C.
'@
    exit 0
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error 'uv was not found. Install it using the Windows instructions in README.md and reopen the terminal.'
    exit 1
}

$hubRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $hubRoot
try {
    switch ($hubAction) {
        'install' { & uv sync --extra dev @hubExtras @hubForward }
        'test' { & uv run --extra dev @hubExtras python -m pytest -q @hubForward }
        { $_ -in @('news-start', 'news-status', 'news-stop') } { & (Join-Path $PSScriptRoot 'tech_worker.ps1') $hubAction.Substring(5) }
        { $_ -in @('trade-start', 'trade-status', 'trade-stop') } { & (Join-Path $PSScriptRoot 'trading_worker.ps1') $hubAction.Substring(6) }
        'translation-model' { & uv run --extra translation @hubExtras python tools/setup_translation.py @hubForward }
        'backup' { & uv run @hubExtras python tools/backup_db.py @hubForward }
        'grok-key' { & uv run @hubExtras python tools/setup_grok_connector.py @hubForward }
        { $_ -in @('weekly', 'monthly', 'plan', 'paper', 'glossary', 'purchase') } {
            & uv run @hubExtras python -m invest_blog.cli preview $hubAction @hubForward
        }
        default { & uv run @hubExtras python -m invest_blog.cli @hubArguments }
    }
    $hubExitCode = $LASTEXITCODE
} catch {
    Write-Error $_ -ErrorAction Continue
    $hubExitCode = 1
} finally {
    Pop-Location
}
exit $hubExitCode
