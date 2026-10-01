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
function global:Invoke-WebRequest {
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
& $GatePath -Execute -EvidenceDirectory $EvidenceDirectory -EvidenceFileName 'report.json' -TimeoutSeconds 1
exit $LASTEXITCODE
'@ | Set-Content -Encoding UTF8 -LiteralPath $executeWrapper

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $executeOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $executeWrapper $scriptPath $tempRoot 2>&1
    $executeExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($executeExitCode -ne 2) {
        throw "Execute with passing public probes and missing token must exit 2, got $executeExitCode."
    }
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
function global:Invoke-WebRequest { throw 'provider-error-secret-sentinel' }
[Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', $null, 'Process')
& $GatePath -Execute -EvidenceDirectory $EvidenceDirectory -EvidenceFileName 'failure.json' -TimeoutSeconds 1
exit $LASTEXITCODE
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

    $statusWrapper = Join-Path $tempRoot 'status-and-opt-in.ps1'
    @'
param(
    [string]$GatePath,
    [string]$EvidenceDirectory,
    [string]$EvidenceFileName,
    [int]$SpringStatus,
    [int]$FrontendStatus,
    [string]$IncludeGroups
)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @"
using System;
public sealed class MockStatusResponse {
    public int StatusCode { get; private set; }
    public MockStatusResponse(int statusCode) { StatusCode = statusCode; }
}
public sealed class MockHttpStatusException : Exception {
    public MockStatusResponse Response { get; private set; }
    public MockHttpStatusException(int statusCode) : base("raw-http-body-must-not-leak") {
        Response = new MockStatusResponse(statusCode);
    }
}
"@
function global:Invoke-WebRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [string]$Method,
        [hashtable]$Headers,
        [int]$TimeoutSec,
        [int]$MaximumRedirection,
        [switch]$UseBasicParsing
    )
    if ($Uri -match '/health/live$') {
        return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"live"}' }
    }
    if ($Uri -match '/health/ready$') {
        return [pscustomobject]@{ StatusCode = 200; Content = '{"status":"ready","checkpoint":true}' }
    }
    if ($Uri -match '/internal/v1/customer-service/health$') {
        throw 'AUTH_HEALTH_MUST_NOT_RUN_WITHOUT_TOKEN'
    }
    $status = if ($Uri -match ':8123') { $SpringStatus } else { $FrontendStatus }
    if ($status -ne 200) { throw [MockHttpStatusException]::new($status) }
    return [pscustomobject]@{ StatusCode = $status; Content = 'raw-http-body-must-not-leak' }
}
function global:uv { throw 'REAL_EXTERNAL_COMMAND_CALLED' }
function global:docker { throw 'REAL_EXTERNAL_COMMAND_CALLED' }
function global:python { throw 'REAL_EXTERNAL_COMMAND_CALLED' }
[Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', $null, 'Process')
$gateParameters = @{
    Execute = $true
    EvidenceDirectory = $EvidenceDirectory
    EvidenceFileName = $EvidenceFileName
    TimeoutSeconds = 1
}
foreach ($group in @($IncludeGroups -split ',' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
    $gateParameters[$group] = $true
}
& $GatePath @gateParameters
exit $LASTEXITCODE
'@ | Set-Content -Encoding UTF8 -LiteralPath $statusWrapper

    function Invoke-StatusScenario {
        param(
            [string]$Name,
            [int]$SpringStatus,
            [int]$FrontendStatus,
            [string[]]$IncludeGroups = @()
        )
        $fileName = "$Name.json"
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $statusWrapper `
            $scriptPath $tempRoot $fileName $SpringStatus $FrontendStatus ($IncludeGroups -join ',') 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        $reportFile = Join-Path $tempRoot $fileName
        if (-not (Test-Path -LiteralPath $reportFile)) {
            throw "Scenario $Name did not create a report: $output"
        }
        $raw = Get-Content -Raw -LiteralPath $reportFile
        if ($raw -match 'raw-http-body-must-not-leak|REAL_EXTERNAL_COMMAND_CALLED') {
            throw "Scenario $Name leaked a body or called a real external command."
        }
        return [pscustomobject]@{
            ExitCode = $exitCode
            Report = ($raw | ConvertFrom-Json)
            Output = ($output | Out-String)
        }
    }

    foreach ($case in @(
        @{ name = 'status-200'; spring = 200; frontend = 200; expectedExit = 2 },
        @{ name = 'status-redirect'; spring = 302; frontend = 399; expectedExit = 2 },
        @{ name = 'status-401'; spring = 401; frontend = 200; expectedExit = 1 },
        @{ name = 'status-404'; spring = 200; frontend = 404; expectedExit = 1 },
        @{ name = 'status-500'; spring = 500; frontend = 500; expectedExit = 1 }
    )) {
        $scenario = Invoke-StatusScenario -Name $case.name -SpringStatus $case.spring `
            -FrontendStatus $case.frontend
        if ($scenario.ExitCode -ne $case.expectedExit) {
            throw "$($case.name) expected exit $($case.expectedExit), got $($scenario.ExitCode)."
        }
        foreach ($stepName in @('spring-http-prerequisite', 'frontend-http-prerequisite')) {
            $step = @($scenario.Report.steps | Where-Object { $_.name -eq $stepName })[0]
            $expectedStatusCode = if ($stepName -eq 'spring-http-prerequisite') { $case.spring } else { $case.frontend }
            if ($expectedStatusCode -ge 200 -and $expectedStatusCode -lt 400) {
                if ($step.status -ne 'passed' -or -not [string]::IsNullOrWhiteSpace([string]$step.stableErrorCode)) {
                    throw "$($case.name) must pass $stepName for HTTP $expectedStatusCode; got $($step.status)/$($step.stableErrorCode)."
                }
            } elseif ($step.status -ne 'failed' -or $step.stableErrorCode -ne 'HTTP_STATUS_UNEXPECTED') {
                throw "$($case.name) must fail $stepName for HTTP $expectedStatusCode."
            }
        }
    }

    $groupDefinitions = [ordered]@{
        IncludeMilvus = @{ prefix = 'milvus-'; destructive = @('milvus-restart-recovery') }
        IncludeCloseAi = @{ prefix = 'closeai-'; destructive = @() }
        IncludeGpu = @{ prefix = 'gpu-'; destructive = @('gpu-oom-mapping') }
        IncludeOss = @{ prefix = 'oss-'; destructive = @('oss-delete') }
        IncludeEndToEnd = @{ prefix = 'e2e-'; destructive = @('e2e-dependency-outage') }
    }
    $optInCases = @()
    foreach ($groupName in $groupDefinitions.Keys) { $optInCases += ,@($groupName) }
    $optInCases += ,@($groupDefinitions.Keys)
    $caseIndex = 0
    foreach ($selectedGroups in $optInCases) {
        $caseIndex++
        $scenario = Invoke-StatusScenario -Name "opt-in-$caseIndex" -SpringStatus 200 `
            -FrontendStatus 200 -IncludeGroups $selectedGroups
        if ($scenario.ExitCode -ne 2) {
            throw "Opt-in case $caseIndex must remain blocked with exit 2."
        }
        foreach ($groupName in $groupDefinitions.Keys) {
            $definition = $groupDefinitions[$groupName]
            $groupSteps = @($scenario.Report.steps | Where-Object { $_.name.StartsWith($definition.prefix) })
            $selected = $selectedGroups -contains $groupName
            foreach ($step in $groupSteps) {
                if (-not $selected) {
                    if ($step.status -ne 'not-run' -or $step.stableErrorCode -ne 'OPT_IN_REQUIRED') {
                        throw "$groupName must remain OPT_IN_REQUIRED when not selected."
                    }
                } elseif ($definition.destructive -contains $step.name) {
                    if ($step.status -ne 'blocked' -or $step.stableErrorCode -ne 'MANUAL_REQUIRED_DESTRUCTIVE') {
                        throw "$($step.name) must remain manual-required when selected."
                    }
                } elseif ($step.status -ne 'blocked' -or $step.stableErrorCode -ne 'SAFE_ENTRYPOINT_UNAVAILABLE') {
                    throw "$($step.name) must be blocked when no safe entrypoint exists."
                }
            }
        }
    }
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "customer-service-rag validation script contracts passed on PowerShell $($PSVersionTable.PSVersion)"
