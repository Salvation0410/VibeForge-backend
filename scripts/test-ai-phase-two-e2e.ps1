param(
    [switch]$Execute,
    [switch]$RunCancellationScenario,
    [switch]$RunLegacyRollbackScenario,
    [string]$SpringBaseUrl = 'http://localhost:8123/api',
    [string]$FrontendBaseUrl = 'http://localhost:5173',
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

# 在任何登录或生成请求前校验参数，避免误操作真实应用。
function Assert-Inputs {
    if (-not $Execute) { return }
    $ids = @($HtmlAppId, $MultiFileAppId, $VueAppId)
    if ($ids | Where-Object { $_ -le 0 }) { throw 'HTML、MULTI_FILE 和 VUE_PROJECT 的应用 ID 必须为正数。' }
    if (@($ids | Sort-Object -Unique).Count -ne 3) { throw 'HTML、MULTI_FILE 和 VUE_PROJECT 的应用 ID 必须互不相同。' }
    if ([string]::IsNullOrWhiteSpace($Account) -or [string]::IsNullOrWhiteSpace($Password)) { throw '账号和密码不能为空。' }
    if ($TimeoutSec -lt 10) { throw 'TimeoutSec 不能小于 10 秒。' }
}

# 创建只用于本轮验收的登录会话，不在输出中记录账号、密码或 Cookie。
function New-Session {
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $body = @{ account = $Account; password = $Password } | ConvertTo-Json
    $login = Invoke-WebRequest -Uri "$($SpringBaseUrl.TrimEnd('/'))/users/login" -Method Post -WebSession $session -ContentType 'application/json; charset=utf-8' -Body $body
    $payload = $login.Content | ConvertFrom-Json
    if ([int]$payload.code -ne 0) { throw '端到端验收账号登录失败。' }
    return $session
}

# 只提取终态数量和稳定错误码，不把流式源码写入验收报告。
function Get-TerminalSummary([string]$Content) {
    $currentEvent = $null
    $doneCount = 0
    $businessErrorCount = 0
    $businessErrorCode = $null
    foreach ($line in ($Content -split "`r?`n")) {
        if ($line -match '^event:\s*(.+?)\s*$') {
            $currentEvent = $Matches[1]
            if ($currentEvent -eq 'done') { $doneCount++ }
            if ($currentEvent -eq 'business-error') { $businessErrorCount++ }
            continue
        }
        if ($currentEvent -eq 'business-error' -and $line -match '^data:\s*(.+)\s*$') {
            try {
                $errorPayload = $Matches[1] | ConvertFrom-Json
                if (-not [string]::IsNullOrWhiteSpace([string]$errorPayload.errorCode)) {
                    $businessErrorCode = [string]$errorPayload.errorCode
                }
            } catch {
                $businessErrorCode = 'INVALID_BUSINESS_ERROR_PAYLOAD'
            }
        }
        if ([string]::IsNullOrWhiteSpace($line)) { $currentEvent = $null }
    }
    return [pscustomobject]@{
        doneCount = $doneCount
        businessErrorCount = $businessErrorCount
        businessErrorCode = $businessErrorCode
    }
}

# 每个场景只接受一个成功终态；业务失败由用户反馈后进入针对性修复。
function Invoke-Scenario([string]$Type, [long]$AppId, [string]$Prompt, $Session, [string]$Phase) {
    $uri = "$($SpringBaseUrl.TrimEnd('/'))/apps/chat/gen/code?appId=$AppId&message=$([uri]::EscapeDataString($Prompt))"
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-WebRequest -Uri $uri -Method Get -WebSession $Session -TimeoutSec $TimeoutSec
    $watch.Stop()
    $terminal = Get-TerminalSummary $response.Content
    $terminalCount = $terminal.doneCount + $terminal.businessErrorCount
    if ($terminalCount -ne 1) { throw "$Type $Phase 预期收到一个终态事件，实际收到 $terminalCount 个。" }
    if ($terminal.businessErrorCount -ne 0) {
        $errorCode = if ([string]::IsNullOrWhiteSpace($terminal.businessErrorCode)) { 'BUSINESS_ERROR' } else { $terminal.businessErrorCode }
        throw "$Type $Phase 收到业务错误：$errorCode"
    }
    $typePath = $Type.ToLowerInvariant()
    $previewSuffix = if ($Type -eq 'VUE_PROJECT') { 'dist/index.html' } else { '' }
    $previewUrl = "$($SpringBaseUrl.TrimEnd('/'))/static/$($typePath)_$AppId/$previewSuffix"
    [ordered]@{
        codeGenType = $Type
        appId = $AppId
        phase = $Phase
        terminalStatus = 'completed'
        doneCount = $terminal.doneCount
        businessErrorCode = $null
        durationMs = $watch.ElapsedMilliseconds
        chatUrl = "$($FrontendBaseUrl.TrimEnd('/'))/apps/$AppId/chat"
        previewUrl = $previewUrl
        downloadUrl = "$($SpringBaseUrl.TrimEnd('/'))/apps/download/$AppId"
        historyUrl = "$($SpringBaseUrl.TrimEnd('/'))/chatHistory/app/$AppId"
        manualChecks = @(
            '未指定的文字、图片、功能和操作方式仍然保留',
            '当前请求成功后预览只刷新一次',
            '首次生成和二次修改完成后聊天历史完整',
            '生成失败、停止或断线后仍保留旧版本和旧预览'
        )
    }
}

Assert-Inputs
if (-not $Execute) {
    Write-Host '当前为 dry-run：已列出三类应用的首次生成、二次修改、停止/断线和 Legacy 回滚检查；只有增加 -Execute 才会发送真实请求。'
    return
}

$session = New-Session
# 三个应用彼此隔离，避免不同生成类型或历史产物互相影响。
$cases = @(
    [pscustomobject]@{ type = 'HTML'; appId = $HtmlAppId; initial = '创建一个咖啡店单页，包含清晰的头图、菜单区域和可点击的查看菜单按钮。'; modify = '只把查看菜单按钮文字改为立即点单，保留原有文字、图片、布局、功能和操作方式。' },
    [pscustomobject]@{ type = 'MULTI_FILE'; appId = $MultiFileAppId; initial = '创建一个由 HTML、CSS、JavaScript 三个文件组成的任务看板，支持新增任务和切换完成状态。'; modify = '只把页面标题改为冲刺看板，保留原有文字、样式、任务数据、功能和操作方式。' },
    [pscustomobject]@{ type = 'VUE_PROJECT'; appId = $VueAppId; initial = '创建一个 Vue 任务清单，支持新增任务和切换完成状态，并包含一张可见的头图。'; modify = '只把主标题改为今日任务，保留原有文字、图片、组件、功能和操作方式。' }
)
$results = [System.Collections.Generic.List[object]]::new()
foreach ($case in $cases) {
    $results.Add((Invoke-Scenario $case.type $case.appId $case.initial $session '首次生成'))
    $results.Add((Invoke-Scenario $case.type $case.appId $case.modify $session '二次修改'))
}
# 停止/断线和 Legacy 回滚需要人工控制时点，脚本只提示并要求保留证据。
if ($RunCancellationScenario) { Write-Host '请在前端生成过程中人工停止或断开连接，并记录旧版本、旧预览和后台取消状态证据。' }
if ($RunLegacyRollbackScenario) { Write-Host '请使用单独配置为 Legacy 的 Spring 环境执行对比脚本，并记录路由与回滚结果。' }
if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = Join-Path (Get-Location) 'target/ai-validation/test-ai-phase-two-e2e.json' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "E2E checklist written to $OutputPath"
