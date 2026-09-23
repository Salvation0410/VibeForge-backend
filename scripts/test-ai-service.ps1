param(
    [switch]$Execute,
    [switch]$IncludeBuild,
    [string]$SpringBaseUrl = 'http://localhost:8123/api',
    [string]$PythonBaseUrl = 'http://localhost:8000',
    [string]$InternalToken = $env:AI_SERVICE_INTERNAL_BEARER_TOKEN,
    [long]$ReadOnlyAppId,
    [long]$VueBuildAppId,
    [string]$CodeGenType = 'VUE_PROJECT',
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Assert-Inputs {
    if (-not $Execute) { return }
    if ([string]::IsNullOrWhiteSpace($InternalToken)) { throw 'InternalToken is required.' }
    if ($ReadOnlyAppId -le 0) { throw 'ReadOnlyAppId must be positive.' }
    if ($IncludeBuild -and $VueBuildAppId -le 0) { throw 'VueBuildAppId must be positive with -IncludeBuild.' }
}
function Invoke-Json([string]$Method, [string]$Uri, [hashtable]$Headers, $Body, [int]$TimeoutSec = 30) {
    $params = @{ Uri = $Uri; Method = $Method; Headers = $Headers; TimeoutSec = $TimeoutSec }
    if ($null -ne $Body) { $params.ContentType = 'application/json; charset=utf-8'; $params.Body = $Body | ConvertTo-Json -Depth 8 }
    try { return Invoke-WebRequest @params }
    catch { return $_.Exception.Response }
}
function Get-Body($Response) {
    if ($Response -is [System.Net.HttpWebResponse]) { $reader = [System.IO.StreamReader]::new($Response.GetResponseStream()); try { return $reader.ReadToEnd() } finally { $reader.Dispose() } }
    return $Response.Content
}
function Assert-BusinessCode($Response, [int]$Expected, [string]$Label) {
    $payload = Get-Body $Response | ConvertFrom-Json
    if ([int]$payload.code -ne $Expected) { throw "$Label expected code $Expected but received $($payload.code)." }
    return $payload
}

Assert-Inputs
if (-not $Execute) {
    Write-Host 'Dry run only. Health, authentication, JSON contract, and optional build checks are listed; rerun with -Execute to call services.'
    return
}

$results = [System.Collections.Generic.List[object]]::new()
$live = Invoke-Json 'GET' "$($PythonBaseUrl.TrimEnd('/'))/health/live" @{} $null
$ready = Invoke-Json 'GET' "$($PythonBaseUrl.TrimEnd('/'))/health/ready" @{} $null
$results.Add([ordered]@{ check = 'python-live'; status = $live.StatusCode })
$results.Add([ordered]@{ check = 'python-ready'; status = $ready.StatusCode })
$body = @{ appId = $ReadOnlyAppId; requestId = "http-$([guid]::NewGuid().ToString('N'))"; toolCallId = 'artifact-context'; toolName = 'artifact_context'; arguments = @{ codeGenType = $CodeGenType } }
$headers = @{ Authorization = "Bearer $InternalToken" }
$valid = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $body
$payload = Assert-BusinessCode $valid 0 'valid invocation'
$results.Add([ordered]@{ check = 'valid-invocation'; status = $valid.StatusCode; dataFields = @($payload.data.PSObject.Properties.Name) })
$missingAuth = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" @{} $body
$results.Add([ordered]@{ check = 'missing-auth'; status = $missingAuth.StatusCode })
$invalidAuth = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" @{ Authorization = 'Bearer invalid-test-token' } $body
$results.Add([ordered]@{ check = 'invalid-auth'; status = $invalidAuth.StatusCode })
$missingApp = @{ requestId = $body.requestId; toolCallId = 'missing-app'; toolName = 'artifact_context'; arguments = $body.arguments }
$missingResponse = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $missingApp
$results.Add([ordered]@{ check = 'missing-app-id'; status = $missingResponse.StatusCode })
if ($IncludeBuild) {
    $buildBody = @{ appId = $VueBuildAppId; requestId = "build-$([guid]::NewGuid().ToString('N'))"; toolCallId = 'project-build'; toolName = 'project_build'; arguments = @{ codeGenType = 'VUE_PROJECT' } }
    $build = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $buildBody 1200
    $buildPayload = Assert-BusinessCode $build 0 'project build'
    $results.Add([ordered]@{ check = 'project-build'; built = $buildPayload.data.built; errorCode = $buildPayload.data.errorCode })
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path (Get-Location) 'target/ai-validation/test-ai-service.json' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "HTTP checklist written to $OutputPath"
