param(
    [switch]$Execute,
    [string]$LegacyBaseUrl = 'http://localhost:8123/api',
    [string]$LangGraphBaseUrl = 'http://localhost:8123/api',
    [long]$LegacyAppId,
    [long]$LangGraphAppId,
    [string]$Account = $env:APP_TEST_USER_ACCOUNT,
    [string]$Password = $env:APP_TEST_USER_PASSWORD,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-TestInputs {
    if (-not $Execute) { return }
    if ($LegacyAppId -le 0 -or $LangGraphAppId -le 0) { throw 'LegacyAppId and LangGraphAppId must be positive.' }
    if ($LegacyAppId -eq $LangGraphAppId) { throw 'LegacyAppId and LangGraphAppId must be distinct.' }
    if ([string]::IsNullOrWhiteSpace($Account) -or [string]::IsNullOrWhiteSpace($Password)) { throw 'Account and Password are required.' }
}

function New-AuthenticatedSession([string]$BaseUrl, [string]$LoginAccount, [string]$LoginPassword) {
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $body = @{ account = $LoginAccount; password = $LoginPassword } | ConvertTo-Json
    $response = Invoke-WebRequest -Uri "$($BaseUrl.TrimEnd('/'))/users/login" -Method Post -WebSession $session -ContentType 'application/json; charset=utf-8' -Body $body
    $payload = $response.Content | ConvertFrom-Json
    if ([int]$payload.code -ne 0) { throw 'Test account login failed.' }
    return $session
}

function Invoke-GenerationStream([string]$BaseUrl, [long]$AppId, [string]$Prompt, $Session) {
    $uri = "$($BaseUrl.TrimEnd('/'))/apps/chat/gen/code?appId=$AppId&message=$([uri]::EscapeDataString($Prompt))"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-WebRequest -Uri $uri -Method Get -WebSession $Session
    $watch.Stop()
    $events = [System.Collections.Generic.List[object]]::new()
    $currentEvent = 'message'
    foreach ($line in ($response.Content -split "`r?`n")) {
        if ($line.StartsWith('event: ')) { $currentEvent = $line.Substring(7).Trim(); $events.Add([pscustomobject]@{ type = $currentEvent }) }
        elseif ($line.StartsWith('data: ') -and $currentEvent -eq 'business-error') { $events.Add([pscustomobject]@{ type = 'business-error' }) }
    }
    return [pscustomobject]@{ events = $events; durationMs = $watch.ElapsedMilliseconds }
}

function ConvertTo-GenerationSummary([string]$Engine, [long]$AppId, [string]$CodeGenType, $Stream, [string]$RequestId) {
    $terminal = @($Stream.events | Where-Object { $_.type -in @('done', 'business-error') })
    if ($terminal.Count -ne 1) { throw "$Engine $CodeGenType returned $($terminal.Count) terminal events." }
    $status = if ($terminal[0].type -eq 'done') { 'completed' } else { 'failed' }
    return [ordered]@{
        engine = $Engine
        appId = $AppId
        codeGenType = $CodeGenType
        requestId = $RequestId
        terminalStatus = $status
        toolNames = @()
        artifactHashes = @()
        buildStatus = 'not-observed-from-public-SSE'
        errorCode = if ($status -eq 'failed') { 'BUSINESS_ERROR' } else { $null }
        durationMs = $Stream.durationMs
    }
}

Assert-TestInputs
if (-not $Execute) {
    Write-Host 'Dry run only. Review the comparison checklist and rerun with -Execute to send requests.'
    return
}

$prompts = @(
    [pscustomobject]@{ type = 'HTML'; text = 'Create a small HTML landing page for a test cafe.' },
    [pscustomobject]@{ type = 'MULTI_FILE'; text = 'Create a three-file test task board with HTML, CSS, and JavaScript.' },
    [pscustomobject]@{ type = 'VUE_PROJECT'; text = 'Create a small Vue task list with add and complete interactions.' }
)
$results = [System.Collections.Generic.List[object]]::new()
$legacySession = New-AuthenticatedSession $LegacyBaseUrl $Account $Password
$langGraphSession = New-AuthenticatedSession $LangGraphBaseUrl $Account $Password
foreach ($case in $prompts) {
    $legacyRequestId = "legacy-$([guid]::NewGuid().ToString('N'))"
    $langGraphRequestId = "langgraph-$([guid]::NewGuid().ToString('N'))"
    $legacyStream = Invoke-GenerationStream $LegacyBaseUrl $LegacyAppId $case.text $legacySession
    $langGraphStream = Invoke-GenerationStream $LangGraphBaseUrl $LangGraphAppId $case.text $langGraphSession
    $results.Add((ConvertTo-GenerationSummary 'legacy' $LegacyAppId $case.type $legacyStream $legacyRequestId))
    $results.Add((ConvertTo-GenerationSummary 'langgraph' $LangGraphAppId $case.type $langGraphStream $langGraphRequestId))
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path (Get-Location) 'target/ai-validation/compare-ai-generation-engines.json' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "Summary written to $OutputPath"
