param(
    [switch]$Execute,
    [switch]$RunCancellationScenario,
    [switch]$RunLegacyRollbackScenario,
    [string]$SpringBaseUrl = 'http://localhost:8123/api',
    [long]$HtmlAppId,
    [long]$MultiFileAppId,
    [long]$VueAppId,
    [string]$Account = $env:APP_TEST_USER_ACCOUNT,
    [string]$Password = $env:APP_TEST_USER_PASSWORD,
    [int]$TimeoutSec = 120,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Inputs {
    if (-not $Execute) { return }
    $ids = @($HtmlAppId, $MultiFileAppId, $VueAppId)
    if ($ids | Where-Object { $_ -le 0 }) { throw 'HTML, MULTI_FILE, and VUE_PROJECT app IDs must be positive.' }
    if (@($ids | Sort-Object -Unique).Count -ne 3) { throw 'HTML, MULTI_FILE, and VUE_PROJECT app IDs must be distinct.' }
    if ([string]::IsNullOrWhiteSpace($Account) -or [string]::IsNullOrWhiteSpace($Password)) { throw 'Account and Password are required.' }
    if ($TimeoutSec -lt 10) { throw 'TimeoutSec must be at least 10 seconds.' }
}
function New-Session {
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $body = @{ account = $Account; password = $Password } | ConvertTo-Json
    $login = Invoke-WebRequest -Uri "$($SpringBaseUrl.TrimEnd('/'))/users/login" -Method Post -WebSession $session -ContentType 'application/json; charset=utf-8' -Body $body
    $payload = $login.Content | ConvertFrom-Json
    if ([int]$payload.code -ne 0) { throw 'E2E test account login failed.' }
    return $session
}
function Invoke-Scenario([string]$Type, [long]$AppId, [string]$Prompt, $Session, [string]$Phase) {
    $uri = "$($SpringBaseUrl.TrimEnd('/'))/apps/chat/gen/code?appId=$AppId&message=$([uri]::EscapeDataString($Prompt))"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-WebRequest -Uri $uri -Method Get -WebSession $Session -TimeoutSec $TimeoutSec
    $watch.Stop()
    $doneCount = ([regex]::Matches($response.Content, '(?m)^event: done\s*$')).Count
    $errorMatches = [regex]::Matches($response.Content, '(?m)^event: business-error\s*$')
    $terminalCount = $doneCount + $errorMatches.Count
    if ($terminalCount -ne 1) { throw "$Type $Phase expected one terminal event, received $terminalCount." }
    [ordered]@{
        codeGenType = $Type
        appId = $AppId
        phase = $Phase
        terminalStatus = if ($doneCount -eq 1) { 'completed' } else { 'failed' }
        doneCount = $doneCount
        businessErrorCode = if ($errorMatches.Count -eq 1) { 'BUSINESS_ERROR' } else { $null }
        durationMs = $watch.ElapsedMilliseconds
        manualChecks = @('preserve unspecified text images features and interactions', 'refresh preview once after current request succeeds', 'retain old active version after failure or stop')
    }
}

Assert-Inputs
if (-not $Execute) {
    Write-Host 'Dry run only. Three app IDs, first generation, second modification, cancellation, and Legacy rollback checks are listed; rerun with -Execute to send requests.'
    return
}

$session = New-Session
$cases = @(
    [pscustomobject]@{ type = 'HTML'; appId = $HtmlAppId; initial = 'Create a test cafe page with a hero image and a menu button.'; modify = 'Change only the menu button label to Order now.' },
    [pscustomobject]@{ type = 'MULTI_FILE'; appId = $MultiFileAppId; initial = 'Create a three-file task board with a title, cards, and add interaction.'; modify = 'Change only the page title to Sprint board.' },
    [pscustomobject]@{ type = 'VUE_PROJECT'; appId = $VueAppId; initial = 'Create a Vue task list with add and complete interactions.'; modify = 'Change only the heading to Today tasks.' }
)
$results = [System.Collections.Generic.List[object]]::new()
foreach ($case in $cases) {
    $results.Add((Invoke-Scenario $case.type $case.appId $case.initial $session 'initial'))
    $results.Add((Invoke-Scenario $case.type $case.appId $case.modify $session 'modification'))
}
if ($RunCancellationScenario) { Write-Host 'Cancellation scenario requires manual client disconnect during a live request; execute it separately and record preview/version evidence.' }
if ($RunLegacyRollbackScenario) { Write-Host 'Legacy rollback scenario requires a separately configured Legacy Spring environment; run the comparison script and record the rollback result.' }
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path (Get-Location) 'target/ai-validation/test-ai-phase-two-e2e.json' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "E2E checklist written to $OutputPath"
