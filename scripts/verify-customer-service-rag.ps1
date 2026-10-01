param(
    [switch]$Execute,
    [switch]$IncludeMilvus,
    [switch]$IncludeCloseAi,
    [switch]$IncludeGpu,
    [switch]$IncludeOss,
    [switch]$IncludeEndToEnd,
    [string]$SpringBaseUrl = 'http://localhost:8123/api',
    [string]$PythonBaseUrl = 'http://localhost:8000',
    [string]$FrontendBaseUrl = 'http://localhost:5173',
    [string]$EvidenceDirectory,
    [string]$EvidenceFileName = 'customer-service-rag-validation.json',
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 5
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$validationGateVersion = '1.0'
$allowedStatuses = @('passed', 'failed', 'blocked', 'not-run')

function Resolve-SafeBaseUri {
    param([Parameter(Mandatory = $true)][string]$Value, [string]$Name)

    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -notin @('http', 'https') -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw "$Name must be an absolute HTTP(S) base URL without user info, query, or fragment."
    }
    return $Value.TrimEnd('/')
}

function New-StepResult {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][ValidateSet('passed', 'failed', 'blocked', 'not-run')][string]$Status,
        [AllowNull()][Nullable[int]]$ExitCode,
        [long]$DurationMs = 0,
        [AllowNull()][string]$StableErrorCode,
        [AllowNull()][string]$DocumentId,
        [AllowNull()][Nullable[long]]$DocumentVersion,
        [AllowNull()][Nullable[int]]$ChunkCount,
        [Parameter(Mandatory = $true)][string]$EvidenceReference
    )

    if ($EvidenceReference -match '(?i)^[a-z]+://|\?|token|authorization|cookie|\.\.') {
        throw 'Evidence reference must be a sanitized relative or logical reference.'
    }
    return [ordered]@{
        name = $Name
        status = $Status
        exitCode = $ExitCode
        durationMs = $DurationMs
        stableErrorCode = $StableErrorCode
        documentId = $DocumentId
        documentVersion = $DocumentVersion
        chunkCount = $ChunkCount
        evidenceReference = $EvidenceReference
    }
}

function Get-StableHttpFailure {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)

    $statusCode = $null
    try {
        if ($null -ne $ErrorRecord.Exception.Response) {
            $statusCode = [int]$ErrorRecord.Exception.Response.StatusCode
        }
    } catch {
        $statusCode = $null
    }
    if ($statusCode -eq 503) { return 'SERVICE_NOT_READY' }
    if ($statusCode -eq 401 -or $statusCode -eq 403) { return 'AUTHENTICATION_REJECTED' }
    if ($null -ne $statusCode) { return 'HTTP_STATUS_UNEXPECTED' }
    if ($ErrorRecord.Exception -is [System.Net.WebException] -and
        $ErrorRecord.Exception.Status -eq [System.Net.WebExceptionStatus]::Timeout) {
        return 'HTTP_TIMEOUT'
    }
    return 'SERVICE_UNREACHABLE'
}

function Get-HttpStatusCode {
    param([System.Management.Automation.ErrorRecord]$ErrorRecord)
    try {
        if ($null -ne $ErrorRecord.Exception.Response) {
            return [int]$ErrorRecord.Exception.Response.StatusCode
        }
    } catch {
        return $null
    }
    return $null
}

function Invoke-HttpValidationStep {
    param(
        [string]$Name,
        [string]$Uri,
        [string]$EvidenceReference,
        [hashtable]$Headers = @{},
        [scriptblock]$ValidateBody,
        [switch]$StatusOnly
    )

    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -Uri $Uri -Method Get -Headers $Headers `
            -TimeoutSec $TimeoutSeconds -MaximumRedirection 0 -UseBasicParsing
        if ([int]$response.StatusCode -lt 200 -or [int]$response.StatusCode -ge 400) {
            return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -StableErrorCode 'HTTP_STATUS_UNEXPECTED' -EvidenceReference $EvidenceReference
        }
        if (-not $StatusOnly) {
            $body = $null
            try {
                $body = $response.Content | ConvertFrom-Json
            } catch {
                return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                    -DurationMs $stopwatch.ElapsedMilliseconds `
                    -StableErrorCode 'HEALTH_RESPONSE_INVALID' -EvidenceReference $EvidenceReference
            }
            $validation = & $ValidateBody $body
            if ($validation.status -ne 'passed') {
                return New-StepResult -Name $Name -Status $validation.status -ExitCode $validation.exitCode `
                    -DurationMs $stopwatch.ElapsedMilliseconds `
                    -StableErrorCode $validation.stableErrorCode -EvidenceReference $EvidenceReference
            }
        }
        return New-StepResult -Name $Name -Status 'passed' -ExitCode 0 `
            -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $null `
            -EvidenceReference $EvidenceReference
    } catch {
        if ($StatusOnly) {
            $statusCode = Get-HttpStatusCode -ErrorRecord $_
            if ($null -ne $statusCode) {
                if ($statusCode -ge 200 -and $statusCode -lt 400) {
                    return New-StepResult -Name $Name -Status 'passed' -ExitCode 0 `
                        -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $null `
                        -EvidenceReference $EvidenceReference
                }
                return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                    -DurationMs $stopwatch.ElapsedMilliseconds `
                    -StableErrorCode 'HTTP_STATUS_UNEXPECTED' `
                    -EvidenceReference $EvidenceReference
            }
        }
        $stableCode = Get-StableHttpFailure -ErrorRecord $_
        $status = if ($stableCode -in @('SERVICE_UNREACHABLE', 'HTTP_TIMEOUT', 'SERVICE_NOT_READY')) {
            'blocked'
        } else {
            'failed'
        }
        $exitCode = if ($status -eq 'blocked') { 2 } else { 1 }
        return New-StepResult -Name $Name -Status $status -ExitCode $exitCode `
            -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $stableCode `
            -EvidenceReference $EvidenceReference
    } finally {
        $stopwatch.Stop()
    }
}

function Test-LiveBody {
    param($Body)
    if ($null -ne $Body -and $Body.PSObject.Properties.Name -contains 'status' -and
        $Body.status -eq 'live') {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'failed'; exitCode = 1; stableErrorCode = 'HEALTH_LIVE_INVALID' }
}

function Test-ReadyBody {
    param($Body)
    if ($null -ne $Body -and $Body.PSObject.Properties.Name -contains 'status' -and
        $Body.PSObject.Properties.Name -contains 'checkpoint' -and
        $Body.status -eq 'ready' -and $Body.checkpoint -eq $true) {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'blocked'; exitCode = 2; stableErrorCode = 'SERVICE_NOT_READY' }
}

function Test-CustomerHealthBody {
    param($Body)

    if ($null -eq $Body -or $Body.PSObject.Properties.Name -notcontains 'ready' -or
        $Body.PSObject.Properties.Name -notcontains 'enabled' -or
        $Body.PSObject.Properties.Name -notcontains 'status' -or
        $Body.PSObject.Properties.Name -notcontains 'reason') {
        return @{ status = 'failed'; exitCode = 1; stableErrorCode = 'CUSTOMER_HEALTH_RESPONSE_INVALID' }
    }
    $reason = [string]$Body.reason
    if ($reason -notmatch '^[A-Z][A-Z0-9_]{0,127}$') {
        $reason = 'CUSTOMER_HEALTH_RESPONSE_INVALID'
    }
    if ($Body.enabled -eq $false) {
        return @{ status = 'blocked'; exitCode = 2; stableErrorCode = $reason }
    }
    if ($Body.ready -eq $true -and $Body.status -eq 'healthy') {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'failed'; exitCode = 1; stableErrorCode = $reason }
}

function New-ManualPlanStep {
    param(
        [string]$Name,
        [string]$Group,
        [string]$Label,
        [string]$EvidenceReference,
        [bool]$Destructive = $false
    )
    return [ordered]@{
        name = $Name
        group = $Group
        label = $Label
        evidenceReference = $EvidenceReference
        destructive = $Destructive
    }
}

function Get-ManualPlan {
    return @(
        (New-ManualPlanStep 'milvus-connectivity' 'Milvus' 'Docker Milvus connection probe' 'manual/milvus/connectivity'),
        (New-ManualPlanStep 'milvus-collection-dimension' 'Milvus' 'Docker Milvus collection and dimension validation' 'manual/milvus/collection-dimension'),
        (New-ManualPlanStep 'milvus-insert-search-delete' 'Milvus' 'Docker Milvus synthetic insert-search-delete with unique prefix and best-effort-cleanup' 'manual/milvus/insert-search-delete'),
        (New-ManualPlanStep 'milvus-staging-alias' 'Milvus' 'Docker Milvus staging collection and alias cutover' 'manual/milvus/staging-alias'),
        (New-ManualPlanStep 'milvus-restart-recovery' 'Milvus' 'Docker Milvus restart recovery (manual-required)' 'manual/milvus/restart-recovery' $true),

        (New-ManualPlanStep 'closeai-embedding-dimension' 'CloseAi' 'CloseAI synthetic embedding dimension' 'manual/closeai/embedding-dimension'),
        (New-ManualPlanStep 'closeai-latency' 'CloseAi' 'CloseAI synthetic embedding latency' 'manual/closeai/latency'),
        (New-ManualPlanStep 'closeai-batch' 'CloseAi' 'CloseAI synthetic embedding batch behavior' 'manual/closeai/batch'),
        (New-ManualPlanStep 'closeai-rate-limit' 'CloseAi' 'CloseAI rate limit mapping' 'manual/closeai/rate-limit'),
        (New-ManualPlanStep 'closeai-log-redaction' 'CloseAi' 'CloseAI log redaction review' 'manual/closeai/log-redaction'),

        (New-ManualPlanStep 'gpu-bge-load' 'Gpu' 'GPU BGE reranker load' 'manual/gpu/bge-load'),
        (New-ManualPlanStep 'gpu-top8-p50' 'Gpu' 'GPU BGE Top8 P50 latency' 'manual/gpu/top8-p50'),
        (New-ManualPlanStep 'gpu-top8-p95' 'Gpu' 'GPU BGE Top8 P95 latency' 'manual/gpu/top8-p95'),
        (New-ManualPlanStep 'gpu-peak-memory' 'Gpu' 'GPU BGE peak memory without serial or unrelated host data' 'manual/gpu/peak-memory'),
        (New-ManualPlanStep 'gpu-concurrency' 'Gpu' 'GPU BGE bounded concurrency' 'manual/gpu/concurrency'),
        (New-ManualPlanStep 'gpu-oom-mapping' 'Gpu' 'GPU BGE OOM mapping without inducing OOM (manual-required)' 'manual/gpu/oom-mapping' $true),

        (New-ManualPlanStep 'oss-upload-pdf' 'Oss' 'OSS synthetic PDF upload' 'manual/oss/upload-pdf'),
        (New-ManualPlanStep 'oss-upload-docx' 'Oss' 'OSS synthetic DOCX upload' 'manual/oss/upload-docx'),
        (New-ManualPlanStep 'oss-upload-md' 'Oss' 'OSS synthetic MD upload' 'manual/oss/upload-md'),
        (New-ManualPlanStep 'oss-upload-txt' 'Oss' 'OSS synthetic TXT upload' 'manual/oss/upload-txt'),
        (New-ManualPlanStep 'oss-short-signed-download' 'Oss' 'OSS short-lived signed download without recording URL' 'manual/oss/signed-download'),
        (New-ManualPlanStep 'oss-locator-hash' 'Oss' 'OSS locator and hash validation' 'manual/oss/locator-hash'),
        (New-ManualPlanStep 'oss-retry-replace' 'Oss' 'OSS retry and replace flow with unique prefix and best-effort-cleanup' 'manual/oss/retry-replace'),
        (New-ManualPlanStep 'oss-disable' 'Oss' 'OSS document disable behavior' 'manual/oss/disable'),
        (New-ManualPlanStep 'oss-delete' 'Oss' 'OSS object delete (manual-required)' 'manual/oss/delete' $true),
        (New-ManualPlanStep 'oss-retention-seven-days' 'Oss' 'OSS 7-day retention evidence' 'manual/oss/retention-seven-days'),

        (New-ManualPlanStep 'e2e-login' 'EndToEnd' 'Spring/Python/Vue E2E login' 'manual/e2e/login'),
        (New-ManualPlanStep 'e2e-anonymous' 'EndToEnd' 'Spring/Python/Vue E2E anonymous rejection' 'manual/e2e/anonymous'),
        (New-ManualPlanStep 'e2e-grounded-answer' 'EndToEnd' 'Spring/Python/Vue E2E answer with sources' 'manual/e2e/grounded-answer'),
        (New-ManualPlanStep 'e2e-no-answer' 'EndToEnd' 'Spring/Python/Vue E2E no-answer behavior' 'manual/e2e/no-answer'),
        (New-ManualPlanStep 'e2e-injection' 'EndToEnd' 'Spring/Python/Vue E2E prompt-injection refusal' 'manual/e2e/injection'),
        (New-ManualPlanStep 'e2e-dependency-outage' 'EndToEnd' 'Spring/Python/Vue dependency outage (manual-required)' 'manual/e2e/dependency-outage' $true),
        (New-ManualPlanStep 'e2e-old-version' 'EndToEnd' 'Spring/Python/Vue old document version rejection' 'manual/e2e/old-version'),
        (New-ManualPlanStep 'e2e-rollback' 'EndToEnd' 'Spring/Python/Vue rollback verification' 'manual/e2e/rollback'),
        (New-ManualPlanStep 'e2e-frontend-error-state' 'EndToEnd' 'Spring/Python/Vue frontend error state' 'manual/e2e/frontend-error-state')
    )
}

function Test-GroupSelected {
    param([string]$Group)
    switch ($Group) {
        'Milvus' { return [bool]$IncludeMilvus }
        'CloseAi' { return [bool]$IncludeCloseAi }
        'Gpu' { return [bool]$IncludeGpu }
        'Oss' { return [bool]$IncludeOss }
        'EndToEnd' { return [bool]$IncludeEndToEnd }
        default { return $false }
    }
}

$manualPlan = @(Get-ManualPlan)
$anyExternalOptIn = $IncludeMilvus -or $IncludeCloseAi -or $IncludeGpu -or $IncludeOss -or $IncludeEndToEnd
if (-not $Execute -and $anyExternalOptIn) {
    Write-Error 'Real dependency opt-in switches require -Execute.'
    exit 1
}

if (-not $Execute) {
    Write-Host 'Dry run only. No network, service, uv, Docker, model, GPU, OSS, browser, or report action will run.'
    Write-Host 'Secrets are read only from the process environment during -Execute and are never printed or stored.'
    Write-Host 'Execution plan:'
    Write-Host ' - Non-secret health: Python /health/live and /health/ready; Spring and Frontend HTTP status only.'
    Write-Host ' - Authenticated customer-service health: environment token only; missing secret becomes blocked.'
    foreach ($step in $manualPlan) {
        Write-Host " - $($step.label) [$($step.group); explicit opt-in; no implicit enablement]"
    }
    Write-Host 'Real checks use only non-sensitive synthetic data, unique prefixes, and best-effort-cleanup when a mature safe entrypoint exists.'
    Write-Host 'Destructive or costly checks such as restart, induced OOM, outage, and delete remain manual-required.'
    return
}

$SpringBaseUrl = Resolve-SafeBaseUri -Value $SpringBaseUrl -Name 'SpringBaseUrl'
$PythonBaseUrl = Resolve-SafeBaseUri -Value $PythonBaseUrl -Name 'PythonBaseUrl'
$FrontendBaseUrl = Resolve-SafeBaseUri -Value $FrontendBaseUrl -Name 'FrontendBaseUrl'
if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
    $EvidenceDirectory = Join-Path $repositoryRoot 'target/ai-validation'
}
if ([string]::IsNullOrWhiteSpace($EvidenceFileName) -or
    [IO.Path]::GetFileName($EvidenceFileName) -ne $EvidenceFileName -or
    [IO.Path]::GetExtension($EvidenceFileName) -ne '.json') {
    throw 'EvidenceFileName must be a JSON leaf filename.'
}

$steps = [System.Collections.Generic.List[object]]::new()
$steps.Add((Invoke-HttpValidationStep -Name 'python-health-live' `
    -Uri "$PythonBaseUrl/health/live" -ValidateBody ${function:Test-LiveBody} `
    -EvidenceReference 'runtime/python-health-live'))
$steps.Add((Invoke-HttpValidationStep -Name 'python-health-ready' `
    -Uri "$PythonBaseUrl/health/ready" -ValidateBody ${function:Test-ReadyBody} `
    -EvidenceReference 'runtime/python-health-ready'))
$steps.Add((Invoke-HttpValidationStep -Name 'spring-http-prerequisite' `
    -Uri $SpringBaseUrl -StatusOnly -EvidenceReference 'runtime/spring-http-status'))
$steps.Add((Invoke-HttpValidationStep -Name 'frontend-http-prerequisite' `
    -Uri $FrontendBaseUrl -StatusOnly -EvidenceReference 'runtime/frontend-http-status'))

$internalToken = [Environment]::GetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', 'Process')
if ([string]::IsNullOrWhiteSpace($internalToken)) {
    $steps.Add((New-StepResult -Name 'authenticated-customer-service-health' `
        -Status 'blocked' -ExitCode 2 -StableErrorCode 'VALIDATION_SECRET_MISSING' `
        -EvidenceReference 'runtime/customer-service-health'))
} else {
    $headers = @{ Authorization = "Bearer $internalToken" }
    $steps.Add((Invoke-HttpValidationStep -Name 'authenticated-customer-service-health' `
        -Uri "$PythonBaseUrl/internal/v1/customer-service/health" -Headers $headers `
        -ValidateBody ${function:Test-CustomerHealthBody} `
        -EvidenceReference 'runtime/customer-service-health'))
    $headers.Clear()
    $internalToken = $null
}

foreach ($planned in $manualPlan) {
    if (-not (Test-GroupSelected -Group $planned.group)) {
        $steps.Add((New-StepResult -Name $planned.name -Status 'not-run' -ExitCode $null `
            -StableErrorCode 'OPT_IN_REQUIRED' -EvidenceReference $planned.evidenceReference))
        continue
    }
    if ($planned.destructive) {
        $steps.Add((New-StepResult -Name $planned.name -Status 'blocked' -ExitCode 2 `
            -StableErrorCode 'MANUAL_REQUIRED_DESTRUCTIVE' `
            -EvidenceReference $planned.evidenceReference))
        continue
    }
    $steps.Add((New-StepResult -Name $planned.name -Status 'blocked' -ExitCode 2 `
        -StableErrorCode 'SAFE_ENTRYPOINT_UNAVAILABLE' `
        -EvidenceReference $planned.evidenceReference))
}

$report = [ordered]@{
    schemaVersion = 'customer-service-rag-validation/v1'
    generatedAt = [DateTime]::UtcNow.ToString('o')
    environment = [ordered]@{
        powerShellVersion = $PSVersionTable.PSVersion.ToString()
        validationGateVersion = $validationGateVersion
    }
    steps = @($steps)
}

[void](New-Item -ItemType Directory -Force -Path $EvidenceDirectory)
$outputPath = Join-Path $EvidenceDirectory $EvidenceFileName
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $outputPath

foreach ($step in $steps) {
    $suffix = if ([string]::IsNullOrWhiteSpace([string]$step.stableErrorCode)) { '' } else { " ($($step.stableErrorCode))" }
    Write-Host "[$($step.status)] $($step.name)$suffix"
}
Write-Host 'Sanitized validation report written to the configured evidence location.'

if (@($steps | Where-Object { $_.status -eq 'failed' }).Count -gt 0) {
    exit 1
}
if (@($steps | Where-Object { $_.status -eq 'blocked' }).Count -gt 0) {
    exit 2
}
exit 0
