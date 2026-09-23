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

# 只有显式执行真实验收时才要求令牌和隔离应用 ID，dry-run 不读取外部服务。
function Assert-Inputs {
    if (-not $Execute) { return }
    if ([string]::IsNullOrWhiteSpace($InternalToken)) { throw 'InternalToken is required.' }
    if ($ReadOnlyAppId -le 0) { throw 'ReadOnlyAppId must be positive.' }
    if ($IncludeBuild -and $VueBuildAppId -le 0) { throw 'VueBuildAppId must be positive with -IncludeBuild.' }
}
# 统一发送 JSON 请求；失败响应由后续断言读取，不在控制台输出响应正文。
function Invoke-Json([string]$Method, [string]$Uri, [hashtable]$Headers, $Body, [int]$TimeoutSec = 30) {
    $params = @{ Uri = $Uri; Method = $Method; Headers = $Headers; TimeoutSec = $TimeoutSec }
    if ($null -ne $Body) { $params.ContentType = 'application/json; charset=utf-8'; $params.Body = $Body | ConvertTo-Json -Depth 8 }
    try { return Invoke-WebRequest @params }
    catch { return $_.Exception.Response }
}
# 兼容成功响应和 Windows PowerShell 的 HttpWebResponse 错误响应。
function Get-Body($Response) {
    if ($Response -is [System.Net.HttpWebResponse]) { $reader = [System.IO.StreamReader]::new($Response.GetResponseStream()); try { return $reader.ReadToEnd() } finally { $reader.Dispose() } }
    return $Response.Content
}
# 校验 Spring 统一业务码，但不把可能含敏感内容的完整响应写入报告。
function Assert-BusinessCode($Response, [int]$Expected, [string]$Label) {
    $payload = Get-Body $Response | ConvertFrom-Json
    if ([int]$payload.code -ne $Expected) { throw "$Label expected code $Expected but received $($payload.code)." }
    return $payload
}
# 对健康检查、鉴权和字段绑定结果执行明确的 HTTP 状态断言。
function Assert-Status($Response, [int]$Expected, [string]$Label) {
    if ($null -eq $Response -or [int]$Response.StatusCode -ne $Expected) {
        $actual = if ($null -eq $Response) { 'no-response' } else { [int]$Response.StatusCode }
        throw "$Label expected HTTP $Expected but received $actual."
    }
}

Assert-Inputs
if (-not $Execute) {
    Write-Host 'Dry run only. Health, authentication, JSON contract, and optional build checks are listed; rerun with -Execute to call services.'
    return
}

$results = [System.Collections.Generic.List[object]]::new()
$live = Invoke-Json 'GET' "$($PythonBaseUrl.TrimEnd('/'))/health/live" @{} $null
$ready = Invoke-Json 'GET' "$($PythonBaseUrl.TrimEnd('/'))/health/ready" @{} $null
Assert-Status $live 200 'health/live'
Assert-Status $ready 200 'health/ready'
$results.Add([ordered]@{ check = 'health/live'; status = [int]$live.StatusCode })
$results.Add([ordered]@{ check = 'health/ready'; status = [int]$ready.StatusCode })
$body = @{ appId = $ReadOnlyAppId; requestId = "http-$([guid]::NewGuid().ToString('N'))"; toolCallId = 'artifact-context'; toolName = 'artifact_context'; arguments = @{ codeGenType = $CodeGenType } }
$headers = @{ Authorization = "Bearer $InternalToken" }
$valid = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $body
Assert-Status $valid 200 'valid invocation'
$payload = Assert-BusinessCode $valid 0 'valid invocation'
$results.Add([ordered]@{ check = 'valid invocation'; status = [int]$valid.StatusCode; dataFields = @($payload.data.PSObject.Properties.Name) })
$missingAuth = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" @{} $body
Assert-Status $missingAuth 401 'missing token'
$results.Add([ordered]@{ check = 'missing token'; status = [int]$missingAuth.StatusCode })
$invalidAuth = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" @{ Authorization = 'Bearer invalid-test-token' } $body
Assert-Status $invalidAuth 401 'invalid token'
$results.Add([ordered]@{ check = 'invalid token'; status = [int]$invalidAuth.StatusCode })
# 使用只读 artifact_context 验证字段绑定失败，避免验收脚本修改项目文件。
$missingApp = @{ requestId = $body.requestId; toolCallId = 'missing-app'; toolName = 'artifact_context'; arguments = $body.arguments }
$missingResponse = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $missingApp
Assert-Status $missingResponse 400 'missing fields'
$results.Add([ordered]@{ check = 'missing fields'; status = [int]$missingResponse.StatusCode })
# 畸形协议和错误脱敏无法安全地由真实服务制造，明确引用离线契约测试结果。
$results.Add([ordered]@{ check = 'stable idempotency errors'; status = 'offline-contract-tests'; note = 'Covered by Spring/Python contract tests without exposing request data.' })
$results.Add([ordered]@{ check = 'sanitization'; status = 'offline-contract-tests'; note = 'Covered by error-boundary tests; response bodies are not written to this report.' })
if ($IncludeBuild) {
    # project_build 可能执行 npm install/build，必须通过独立开关和专用应用 ID 显式启用。
    $buildBody = @{ appId = $VueBuildAppId; requestId = "build-$([guid]::NewGuid().ToString('N'))"; toolCallId = 'project-build'; toolName = 'project_build'; arguments = @{ codeGenType = 'VUE_PROJECT' } }
    $build = Invoke-Json 'POST' "$($SpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" $headers $buildBody 1200
    $buildPayload = Assert-BusinessCode $build 0 'project build'
    $results.Add([ordered]@{ check = 'project build'; built = $buildPayload.data.built; errorCode = $buildPayload.data.errorCode })
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path (Get-Location) 'target/ai-validation/test-ai-service.json' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "HTTP checklist written to $OutputPath"
