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
    [string]$ReportPath,
    [string]$EvidenceDirectory,
    [string]$EvidenceFileName,
    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 5
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$validationGateVersion = '1.1'
$allowedStatuses = @('passed', 'failed', 'blocked', 'not-run')
$maxJsonResponseBytes = 65536

function Test-LoopbackAuthority {
    param([Parameter(Mandatory = $true)][string]$Value)
    $match = [regex]::Match($Value, '^[A-Za-z][A-Za-z0-9+.-]*://([^/]+)')
    if (-not $match.Success) { return $false }
    $authority = $match.Groups[1].Value
    if ($authority.StartsWith('[')) {
        $closing = $authority.IndexOf(']')
        if ($closing -lt 0) { return $false }
        $hostLiteral = $authority.Substring(0, $closing + 1)
        $suffix = $authority.Substring($closing + 1)
        if ($suffix.Length -gt 0 -and $suffix -notmatch '^:\d+$') { return $false }
        return $hostLiteral -ceq '[::1]'
    }
    $hostLiteral = ($authority -split ':', 2)[0]
    if ($hostLiteral -ceq 'localhost') { return $true }
    if ($hostLiteral -notmatch '^127(?:\.\d{1,3}){3}$') { return $false }
    $address = $null
    return [System.Net.IPAddress]::TryParse($hostLiteral, [ref]$address) -and
        $address.GetAddressBytes()[0] -eq 127
}

function Resolve-SafeRequestUri {
    param([Parameter(Mandatory = $true)][string]$Value, [string]$Name)
    $uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -notin @('http', 'https') -or
        -not [string]::IsNullOrEmpty($uri.UserInfo) -or
        -not [string]::IsNullOrEmpty($uri.Query) -or
        -not [string]::IsNullOrEmpty($uri.Fragment)) {
        throw 'URL_POLICY_INVALID'
    }
    if ($uri.Scheme -eq 'http' -and -not (Test-LoopbackAuthority -Value $Value)) {
        throw 'REMOTE_HTTP_NOT_ALLOWED'
    }
    return $uri
}

function Resolve-SafeBaseUri {
    param([Parameter(Mandatory = $true)][string]$Value, [string]$Name)
    $uri = Resolve-SafeRequestUri -Value $Value -Name $Name
    return $uri.AbsoluteUri.TrimEnd('/')
}

function Assert-SafeEvidenceReference {
    param([Parameter(Mandatory = $true)][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains('\') -or
        $Value.Contains(':') -or $Value.Contains('?') -or $Value.Contains('#') -or
        [IO.Path]::IsPathRooted($Value)) {
        throw 'EVIDENCE_REFERENCE_INVALID'
    }
    $decoded = $null
    try { $decoded = [Uri]::UnescapeDataString($Value) }
    catch { throw 'EVIDENCE_REFERENCE_INVALID' }
    if ($decoded -cne $Value -or $decoded.Contains('\') -or $decoded.Contains(':') -or
        $decoded.Contains('?') -or $decoded.Contains('#') -or [IO.Path]::IsPathRooted($decoded)) {
        throw 'EVIDENCE_REFERENCE_INVALID'
    }
    $segments = @($decoded -split '/')
    if ($segments.Count -eq 0 -or @($segments | Where-Object {
        [string]::IsNullOrWhiteSpace($_) -or $_ -in @('.', '..') -or
        $_ -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$'
    }).Count -gt 0) {
        throw 'EVIDENCE_REFERENCE_INVALID'
    }
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

    Assert-SafeEvidenceReference -Value $EvidenceReference
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

function Test-ExactJsonObject {
    param($Value, [string[]]$RequiredFields)
    if ($null -eq $Value -or $Value.GetType().FullName -ne 'System.Management.Automation.PSCustomObject') {
        return $false
    }
    $actual = @($Value.PSObject.Properties.Name | Sort-Object)
    $required = @($RequiredFields | Sort-Object)
    return @(Compare-Object $required $actual).Count -eq 0
}

function Test-ExactType {
    param($Value, [Type]$ExpectedType)
    return $null -ne $Value -and $Value.GetType() -eq $ExpectedType
}

function Read-BoundedJsonBody {
    param(
        [Parameter(Mandatory = $true)][System.Net.Http.HttpResponseMessage]$Response,
        [Parameter(Mandatory = $true)][System.Threading.CancellationToken]$CancellationToken
    )
    $declaredLength = $Response.Content.Headers.ContentLength
    if ($null -ne $declaredLength -and [long]$declaredLength -gt $maxJsonResponseBytes) {
        return @{ error = 'HTTP_RESPONSE_TOO_LARGE'; body = $null }
    }
    $stream = $null
    $memory = $null
    try {
        $stream = $Response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $memory = [IO.MemoryStream]::new()
        $buffer = New-Object byte[] 8192
        while ($true) {
            $read = $stream.ReadAsync($buffer, 0, $buffer.Length, $CancellationToken).GetAwaiter().GetResult()
            if ($read -eq 0) { break }
            if ($memory.Length + $read -gt $maxJsonResponseBytes) {
                return @{ error = 'HTTP_RESPONSE_TOO_LARGE'; body = $null }
            }
            $memory.Write($buffer, 0, $read)
        }
        try {
            $encoding = [Text.UTF8Encoding]::new($false, $true)
            $json = $encoding.GetString($memory.ToArray())
            $body = $json | ConvertFrom-Json
        } catch {
            return @{ error = 'HEALTH_RESPONSE_INVALID'; body = $null }
        }
        return @{ error = $null; body = $body }
    } finally {
        if ($null -ne $memory) { $memory.Dispose() }
        if ($null -ne $stream) { $stream.Dispose() }
    }
}

function Test-TimeoutException {
    param([Parameter(Mandatory = $true)][Exception]$Exception)
    $current = $Exception
    while ($null -ne $current) {
        if ($current -is [OperationCanceledException] -or
            $current -is [System.Threading.Tasks.TaskCanceledException]) {
            return $true
        }
        $current = $current.InnerException
    }
    return $false
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
    $handler = $null
    $client = $null
    $request = $null
    $response = $null
    $cancellation = $null
    try {
        $safeUri = Resolve-SafeRequestUri -Value $Uri -Name $Name
        $handler = [System.Net.Http.HttpClientHandler]::new()
        $handler.AllowAutoRedirect = $false
        $client = [System.Net.Http.HttpClient]::new($handler)
        $client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
        $cancellation = [System.Threading.CancellationTokenSource]::new()
        $cancellation.CancelAfter([TimeSpan]::FromSeconds($TimeoutSeconds))
        $request = [System.Net.Http.HttpRequestMessage]::new(
            [System.Net.Http.HttpMethod]::Get, $safeUri)
        foreach ($entry in $Headers.GetEnumerator()) {
            [void]$request.Headers.TryAddWithoutValidation($entry.Key, [string]$entry.Value)
        }
        $response = $client.SendAsync(
            $request,
            [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead,
            $cancellation.Token).GetAwaiter().GetResult()
        $statusCode = [int]$response.StatusCode
        if ($StatusOnly) {
            if ($statusCode -ge 200 -and $statusCode -lt 400) {
                return New-StepResult -Name $Name -Status 'passed' -ExitCode 0 `
                    -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $null `
                    -EvidenceReference $EvidenceReference
            }
            return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -StableErrorCode 'HTTP_STATUS_UNEXPECTED' -EvidenceReference $EvidenceReference
        }
        if ($statusCode -ne 200) {
            return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -StableErrorCode 'HTTP_STATUS_UNEXPECTED' -EvidenceReference $EvidenceReference
        }
        $readResult = Read-BoundedJsonBody -Response $response `
            -CancellationToken $cancellation.Token
        if (-not [string]::IsNullOrWhiteSpace([string]$readResult.error)) {
            return New-StepResult -Name $Name -Status 'failed' -ExitCode 1 `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -StableErrorCode $readResult.error -EvidenceReference $EvidenceReference
        }
        $validation = & $ValidateBody $readResult.body
        if ($validation.status -ne 'passed') {
            return New-StepResult -Name $Name -Status $validation.status -ExitCode $validation.exitCode `
                -DurationMs $stopwatch.ElapsedMilliseconds `
                -StableErrorCode $validation.stableErrorCode -EvidenceReference $EvidenceReference
        }
        return New-StepResult -Name $Name -Status 'passed' -ExitCode 0 `
            -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $null `
            -EvidenceReference $EvidenceReference
    } catch {
        $stableCode = if (Test-TimeoutException -Exception $_.Exception) {
            'HTTP_TIMEOUT'
        } else {
            'SERVICE_UNREACHABLE'
        }
        return New-StepResult -Name $Name -Status 'blocked' -ExitCode 2 `
            -DurationMs $stopwatch.ElapsedMilliseconds -StableErrorCode $stableCode `
            -EvidenceReference $EvidenceReference
    } finally {
        $stopwatch.Stop()
        if ($null -ne $response) { $response.Dispose() }
        if ($null -ne $request) { $request.Dispose() }
        if ($null -ne $cancellation) { $cancellation.Dispose() }
        if ($null -ne $client) { $client.Dispose() }
        if ($null -ne $handler) { $handler.Dispose() }
    }
}

function Test-LiveBody {
    param($Body)
    if ((Test-ExactJsonObject -Value $Body -RequiredFields @('status')) -and
        (Test-ExactType -Value $Body.status -ExpectedType ([string])) -and $Body.status -eq 'live') {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'failed'; exitCode = 1; stableErrorCode = 'HEALTH_RESPONSE_INVALID' }
}

function Test-ReadyBody {
    param($Body)
    if (-not (Test-ExactJsonObject -Value $Body -RequiredFields @('status', 'checkpoint')) -or
        -not (Test-ExactType -Value $Body.status -ExpectedType ([string])) -or
        -not (Test-ExactType -Value $Body.checkpoint -ExpectedType ([bool]))) {
        return @{ status = 'failed'; exitCode = 1; stableErrorCode = 'HEALTH_RESPONSE_INVALID' }
    }
    if ($Body.status -eq 'ready' -and $Body.checkpoint -eq $true) {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'blocked'; exitCode = 2; stableErrorCode = 'SERVICE_NOT_READY' }
}

function Test-CustomerHealthBody {
    param($Body)
    $topFields = @('enabled', 'status', 'reason', 'ready', 'degraded', 'dependencies')
    $dependencyFields = @(
        'answerService', 'answerModel', 'embedding', 'etl',
        'leaseValidator', 'milvus', 'reranker')
    if (-not (Test-ExactJsonObject -Value $Body -RequiredFields $topFields) -or
        -not (Test-ExactType -Value $Body.enabled -ExpectedType ([bool])) -or
        -not (Test-ExactType -Value $Body.status -ExpectedType ([string])) -or
        -not (Test-ExactType -Value $Body.reason -ExpectedType ([string])) -or
        -not (Test-ExactType -Value $Body.ready -ExpectedType ([bool])) -or
        -not (Test-ExactType -Value $Body.degraded -ExpectedType ([bool])) -or
        -not (Test-ExactJsonObject -Value $Body.dependencies -RequiredFields $dependencyFields) -or
        @($dependencyFields | Where-Object {
            -not (Test-ExactType -Value $Body.dependencies.$_ -ExpectedType ([bool]))
        }).Count -gt 0 -or $Body.reason -notmatch '^[A-Z][A-Z0-9_]{0,127}$') {
        return @{ status = 'failed'; exitCode = 1; stableErrorCode = 'HEALTH_RESPONSE_INVALID' }
    }
    $reason = $Body.reason
    if ($Body.enabled -eq $false) {
        return @{ status = 'blocked'; exitCode = 2; stableErrorCode = $reason }
    }
    $allDependenciesReady = @($dependencyFields | Where-Object {
        $Body.dependencies.$_ -ne $true
    }).Count -eq 0
    if ($Body.ready -eq $true -and $Body.degraded -eq $false -and
        $Body.status -eq 'healthy' -and $allDependenciesReady) {
        return @{ status = 'passed'; exitCode = 0; stableErrorCode = $null }
    }
    return @{ status = 'failed'; exitCode = 1; stableErrorCode = $reason }
}

function Resolve-ReportOutputPath {
    if (-not [string]::IsNullOrWhiteSpace($ReportPath)) {
        if (-not [string]::IsNullOrWhiteSpace($EvidenceDirectory) -or
            -not [string]::IsNullOrWhiteSpace($EvidenceFileName)) {
            throw 'REPORT_PATH_CONFLICT'
        }
        $resolved = [IO.Path]::GetFullPath($ReportPath)
    } else {
        $directory = if ([string]::IsNullOrWhiteSpace($EvidenceDirectory)) {
            Join-Path $repositoryRoot 'target/ai-validation'
        } else {
            [IO.Path]::GetFullPath($EvidenceDirectory)
        }
        $fileName = $EvidenceFileName
        if ([string]::IsNullOrWhiteSpace($fileName)) {
            $timestamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
            $runId = [guid]::NewGuid().ToString('N')
            $fileName = "customer-service-rag-validation-$timestamp-$runId.json"
        }
        if ([IO.Path]::GetFileName($fileName) -ne $fileName -or
            [IO.Path]::GetExtension($fileName) -ne '.json') {
            throw 'REPORT_FILE_NAME_INVALID'
        }
        $resolved = Join-Path $directory $fileName
    }
    if (Test-Path -LiteralPath $resolved) { throw 'REPORT_PATH_EXISTS' }
    return $resolved
}

function Write-AtomicJsonReport {
    param(
        [Parameter(Mandatory = $true)]$Report,
        [Parameter(Mandatory = $true)][string]$OutputPath
    )
    $directory = Split-Path -Parent $OutputPath
    if ([string]::IsNullOrWhiteSpace($directory)) { $directory = (Get-Location).Path }
    [void](New-Item -ItemType Directory -Force -Path $directory)
    if (Test-Path -LiteralPath $OutputPath) { throw 'REPORT_PATH_EXISTS' }
    $tempPath = Join-Path $directory ('.' + [IO.Path]::GetFileName($OutputPath) + '.' +
        [guid]::NewGuid().ToString('N') + '.tmp')
    $stream = $null
    $writer = $null
    try {
        $json = $Report | ConvertTo-Json -Depth 8
        $stream = [IO.File]::Open(
            $tempPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
        $writer.Write($json)
        $writer.Flush()
        $stream.Flush($true)
        $writer.Dispose()
        $writer = $null
        $stream = $null
        if (Test-Path -LiteralPath $OutputPath) { throw 'REPORT_PATH_EXISTS' }
        try { [IO.File]::Move($tempPath, $OutputPath) }
        catch {
            if (Test-Path -LiteralPath $OutputPath) { throw 'REPORT_PATH_EXISTS' }
            throw 'REPORT_WRITE_FAILED'
        }
    } finally {
        if ($null -ne $writer) { $writer.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }
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
    Write-Host 'OPT_IN_REQUIRES_EXECUTE'
    exit 1
}

try {
    $SpringBaseUrl = Resolve-SafeBaseUri -Value $SpringBaseUrl -Name 'SpringBaseUrl'
    $PythonBaseUrl = Resolve-SafeBaseUri -Value $PythonBaseUrl -Name 'PythonBaseUrl'
    $FrontendBaseUrl = Resolve-SafeBaseUri -Value $FrontendBaseUrl -Name 'FrontendBaseUrl'
} catch {
    Write-Host $_.Exception.Message
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

try { $outputPath = Resolve-ReportOutputPath }
catch {
    Write-Host $_.Exception.Message
    exit 1
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
    try {
        $customerHealthUri = (Resolve-SafeRequestUri `
            -Value "$PythonBaseUrl/internal/v1/customer-service/health" `
            -Name 'authenticated-customer-service-health').AbsoluteUri
    } catch {
        $internalToken = $null
        Write-Host $_.Exception.Message
        exit 1
    }
    $headers = @{ Authorization = "Bearer $internalToken" }
    $steps.Add((Invoke-HttpValidationStep -Name 'authenticated-customer-service-health' `
        -Uri $customerHealthUri -Headers $headers `
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

try { Write-AtomicJsonReport -Report $report -OutputPath $outputPath }
catch {
    Write-Host $_.Exception.Message
    exit 1
}

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
