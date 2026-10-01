Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path $PSScriptRoot 'verify-customer-service-rag.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw 'Missing customer service RAG validation gate.'
}

$source = Get-Content -Raw -LiteralPath $scriptPath
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) {
    throw "Validation gate has PowerShell parse errors: $($parseErrors[0].Message)"
}

foreach ($marker in @(
    '[switch]$Execute', '[switch]$IncludeMilvus', '[switch]$IncludeCloseAi',
    '[switch]$IncludeGpu', '[switch]$IncludeOss', '[switch]$IncludeEndToEnd',
    '[string]$SpringBaseUrl', '[string]$PythonBaseUrl', '[string]$FrontendBaseUrl',
    '[string]$EvidenceDirectory', '[string]$EvidenceFileName', '[int]$TimeoutSeconds',
    'AI_SERVICE_INTERNAL_BEARER_TOKEN', '/health/live', '/health/ready',
    '/internal/v1/customer-service/health', 'Invoke-WebRequest',
    'schemaVersion', 'generatedAt', 'environment', 'steps',
    'stableErrorCode', 'evidenceReference', 'documentVersion', 'chunkCount',
    'Docker Milvus', 'CloseAI', 'GPU BGE', 'OSS synthetic', 'Spring/Python/Vue E2E',
    'manual-required', 'best-effort-cleanup'
)) {
    if ($source -notmatch [regex]::Escape($marker)) {
        throw "Validation gate misses required contract marker: $marker"
    }
}

foreach ($forbiddenParameter in @('Token', 'ApiKey', 'Secret', 'Cookie', 'Authorization')) {
    if ($source -match "(?im)^\s*\[[^\]]+\]\s*`$$forbiddenParameter\b") {
        throw "Validation gate must not accept plaintext secret parameter: $forbiddenParameter"
    }
}
if ($source -match '(?i)Get-Content[^\r\n]*\.env') {
    throw 'Validation gate must not read .env content.'
}
if ($source -match '(?i)(docker\s+(restart|rm|stop)|uv\s+run|Invoke-RestMethod)') {
    throw 'Validation gate must not embed unsafe external execution paths.'
}

$shellPath = (Get-Process -Id $PID).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("customer-rag-gate-test-" + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $tempRoot)
try {
    $dryRunWrapper = Join-Path $tempRoot 'dry-run.ps1'
    @'
param([string]$GatePath)
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest { throw 'NETWORK_CALLED_IN_DRY_RUN' }
function Invoke-RestMethod { throw 'REST_CALLED_IN_DRY_RUN' }
function uv { throw 'UV_CALLED_IN_DRY_RUN' }
function docker { throw 'DOCKER_CALLED_IN_DRY_RUN' }
[Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', 'secret-sentinel-value', 'Process')
. $GatePath
'@ | Set-Content -Encoding UTF8 -LiteralPath $dryRunWrapper

    $dryOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $dryRunWrapper $scriptPath 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Dry run failed: $dryOutput" }
    $dryText = "$dryOutput"
    foreach ($marker in @('Dry run only', 'Docker Milvus', 'CloseAI', 'GPU BGE', 'OSS synthetic', 'Spring/Python/Vue E2E')) {
        if ($dryText -notmatch [regex]::Escape($marker)) { throw "Dry run misses plan item: $marker" }
    }
    foreach ($forbidden in @('secret-sentinel-value', 'NETWORK_CALLED_IN_DRY_RUN', 'UV_CALLED_IN_DRY_RUN', 'DOCKER_CALLED_IN_DRY_RUN')) {
        if ($dryText -match [regex]::Escape($forbidden)) { throw "Dry run exposed or executed forbidden value: $forbidden" }
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $invalidOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $scriptPath -IncludeMilvus 2>&1
    $invalidExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    $invalidText = ($invalidOutput | Out-String) -replace '\s+', ' '
    if ($invalidExitCode -eq 0 -or $invalidText -notmatch 'require -Execute') {
        throw 'Real dependency opt-ins must be rejected without -Execute.'
    }

    $executeWrapper = Join-Path $tempRoot 'execute.ps1'
    @'
param([string]$GatePath, [string]$EvidenceDirectory)
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [string]$Method,
        [hashtable]$Headers,
        [int]$TimeoutSec,
        [int]$MaximumRedirection,
        [switch]$UseBasicParsing
    )
    if ($Uri -match '/health/live$') {
        return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"live","ignored":"not-recorded"}' }
    }
    if ($Uri -match '/health/ready$') {
        return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"ready","checkpoint":true,"ignored":"not-recorded"}' }
    }
    if ($Uri -match '/internal/v1/customer-service/health$') {
        throw 'AUTH_HEALTH_MUST_NOT_RUN_WITHOUT_TOKEN'
    }
    return [pscustomobject]@{ StatusCode = 200; Content = 'sensitive-body-must-not-be-recorded' }
}
[Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', $null, 'Process')
. $GatePath -Execute -EvidenceDirectory $EvidenceDirectory -EvidenceFileName 'report.json' -TimeoutSeconds 1
'@ | Set-Content -Encoding UTF8 -LiteralPath $executeWrapper

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $executeOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $executeWrapper $scriptPath $tempRoot 2>&1
    $executeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    $reportPath = Join-Path $tempRoot 'report.json'
    if (-not (Test-Path -LiteralPath $reportPath)) { throw 'Execute did not write the validation report.' }
    $reportText = Get-Content -Raw -LiteralPath $reportPath
    foreach ($forbidden in @('sensitive-body-must-not-be-recorded', 'AUTH_HEALTH_MUST_NOT_RUN_WITHOUT_TOKEN', 'secret-sentinel-value')) {
        if ($reportText -match [regex]::Escape($forbidden)) { throw "Report leaked forbidden content: $forbidden" }
    }
    $report = $reportText | ConvertFrom-Json
    $topLevel = @($report.PSObject.Properties.Name | Sort-Object)
    $expectedTopLevel = @('environment', 'generatedAt', 'schemaVersion', 'steps')
    if (@(Compare-Object $expectedTopLevel $topLevel).Count -ne 0) {
        throw "Report top-level whitelist changed: $($topLevel -join ', ')"
    }
    $expectedEnvironment = @('powerShellVersion', 'validationGateVersion')
    $environmentFields = @($report.environment.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object $expectedEnvironment $environmentFields).Count -ne 0) {
        throw "Environment whitelist changed: $($environmentFields -join ', ')"
    }
    $expectedStepFields = @(
        'chunkCount', 'documentId', 'documentVersion', 'durationMs',
        'evidenceReference', 'exitCode', 'name', 'stableErrorCode', 'status'
    )
    foreach ($step in $report.steps) {
        $actualFields = @($step.PSObject.Properties.Name | Sort-Object)
        if (@(Compare-Object $expectedStepFields $actualFields).Count -ne 0) {
            throw "Step whitelist changed for $($step.name): $($actualFields -join ', ')"
        }
        if ($step.status -notin @('passed', 'failed', 'blocked', 'not-run')) {
            throw "Invalid status for $($step.name): $($step.status)"
        }
        if ($step.evidenceReference -match '(?i)^[a-z]+://|\?|token|authorization|cookie') {
            throw "Unsafe evidence reference for $($step.name)"
        }
    }
    $authStep = @($report.steps | Where-Object { $_.name -eq 'authenticated-customer-service-health' })
    if ($authStep.Count -ne 1 -or $authStep[0].status -ne 'blocked' -or
        $authStep[0].stableErrorCode -ne 'VALIDATION_SECRET_MISSING') {
        throw 'Missing internal token must produce one stable blocked health step.'
    }

    $failureWrapper = Join-Path $tempRoot 'failure.ps1'
    @'
param([string]$GatePath, [string]$EvidenceDirectory)
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest { throw 'provider-error-secret-sentinel' }
[Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', $null, 'Process')
. $GatePath -Execute -EvidenceDirectory $EvidenceDirectory -EvidenceFileName 'failure.json' -TimeoutSeconds 1
'@ | Set-Content -Encoding UTF8 -LiteralPath $failureWrapper
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    [void](& $shellPath -NoProfile -ExecutionPolicy Bypass -File $failureWrapper $scriptPath $tempRoot 2>&1)
    $ErrorActionPreference = $previousErrorActionPreference
    $failureText = Get-Content -Raw -LiteralPath (Join-Path $tempRoot 'failure.json')
    if ($failureText -match 'provider-error-secret-sentinel') {
        throw 'Raw HTTP exception text must never enter the report.'
    }
    $failureReport = $failureText | ConvertFrom-Json
    $networkSteps = @($failureReport.steps | Select-Object -First 4)
    if (@($networkSteps | Where-Object { $_.status -ne 'blocked' -or $_.stableErrorCode -ne 'SERVICE_UNREACHABLE' }).Count -ne 0) {
        throw 'HTTP exceptions must map to stable blocked SERVICE_UNREACHABLE results.'
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "customer-service-rag validation script contracts passed on PowerShell $($PSVersionTable.PSVersion)"
