Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedScripts = @(
    'compare-ai-generation-engines.ps1',
    'test-ai-service.ps1',
    'test-ai-phase-two-e2e.ps1'
)

foreach ($name in $expectedScripts) {
    $path = Join-Path $PSScriptRoot $name
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing validation script: $name" }
    $source = Get-Content -Raw -LiteralPath $path
    if ($source -notmatch '\[switch\]\$Execute') { throw "$name must require -Execute" }
    if ($source -notmatch 'Set-StrictMode -Version Latest') { throw "$name must enable strict mode" }
    if ($source -notmatch '\$ErrorActionPreference\s*=\s*["'']Stop["'']') { throw "$name must stop on errors" }
    if ($source -notmatch 'target[\\/]ai-validation') { throw "$name must use an ignored validation output directory" }
    if ($source -match 'Write-(Host|Output).*\$.*(Token|Password|Cookie)') {
        throw "$name may expose credentials"
    }
}

$http = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'test-ai-service.ps1')
# HTTP 验收报告必须能区分健康、鉴权、字段绑定、幂等错误和脱敏边界。
foreach ($label in @('health/live', 'health/ready', 'missing token', 'invalid token', 'valid invocation', 'missing fields', 'stable idempotency errors', 'sanitization')) {
    if ($http -notmatch [regex]::Escape($label)) { throw "HTTP checklist misses label: $label" }
}
# 静态阻止脚本把令牌、认证头或完整响应正文打印到控制台。
foreach ($forbidden in @('Write-Host.*InternalToken', 'Write-Host.*Authorization', 'Write-Host.*Response.Content')) {
    if ($http -match $forbidden) { throw "HTTP checklist may expose sensitive response data: $forbidden" }
}

$comparison = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'compare-ai-generation-engines.ps1')
foreach ($field in @('engine','appId','codeGenType','requestId','terminalStatus','toolNames','artifactHashes','buildStatus','errorCode','durationMs')) {
    if ($comparison -notmatch [regex]::Escape($field)) { throw "Comparison report misses $field" }
}
foreach ($forbidden in @('prompt =','source =','cookie =','token =','toolArguments =')) {
    if ($comparison -match [regex]::Escape($forbidden)) { throw "Comparison report contains forbidden field $forbidden" }
}

$e2e = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'test-ai-phase-two-e2e.ps1')
# 三类型验收入口必须同时包含自动终态断言和中文人工检查清单。
foreach ($marker in @(
    'HTML', 'MULTI_FILE', 'VUE_PROJECT', 'business-error', 'manualChecks',
    'RunCancellationScenario', 'RunLegacyRollbackScenario', '首次生成', '二次修改',
    '未指定的文字、图片、功能和操作方式仍然保留', '旧版本', 'previewUrl',
    'downloadUrl', 'historyUrl', '业务错误'
)) {
    if ($e2e -notmatch [regex]::Escape($marker)) { throw "E2E script misses $marker" }
}

# 重复应用 ID 必须在登录或发送生成请求前被拒绝。
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$duplicateResult = & powershell -NoProfile -File (Join-Path $PSScriptRoot 'test-ai-phase-two-e2e.ps1') `
    -Execute -HtmlAppId 1 -MultiFileAppId 1 -VueAppId 2 2>&1
$duplicateExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
if ($duplicateExitCode -eq 0 -or "$duplicateResult" -notmatch '必须互不相同') {
    throw 'E2E script must reject duplicate application IDs before network access'
}

'AI validation script static checks passed'
