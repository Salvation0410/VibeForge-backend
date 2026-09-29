param(
    [switch]$Execute,
    [switch]$IncludeRedis,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$focusedTests = 'LangGraphAiGenerationGatewayTest,DelegatingAiGenerationGatewayTest,AppServiceGenerationCancellationTest,VueProjectBuilderTest'
$repositoryRoot = Split-Path -Parent $PSScriptRoot

function Resolve-MavenCommand {
    $command = Get-Command 'mvn.cmd' -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        $command = Get-Command 'mvn' -ErrorAction SilentlyContinue
    }
    if ($null -eq $command) {
        throw 'Maven command was not found on PATH.'
    }
    return $command.Source
}

function Invoke-ValidationStep(
    [string]$Name,
    [string]$CommandLabel,
    [string]$FilePath,
    [string[]]$ArgumentList,
    [hashtable]$EnvironmentVariables = @{}
) {
    Write-Host "`n[$Name] $CommandLabel"
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $previousValues = @{}
    $previousErrorActionPreference = $ErrorActionPreference
    $exitCode = 1

    try {
        foreach ($entry in $EnvironmentVariables.GetEnumerator()) {
            $previousValues[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
            [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, 'Process')
        }

        # Native tools legitimately write warnings to stderr; only their exit code decides the step result.
        $ErrorActionPreference = 'Continue'
        & $FilePath @ArgumentList 2>&1 | ForEach-Object { Write-Host $_ }
        $exitCode = $LASTEXITCODE
    } catch {
        Write-Warning "$Name could not start or complete: $($_.Exception.Message)"
        $exitCode = 1
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
        foreach ($entry in $EnvironmentVariables.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable(
                $entry.Key,
                $previousValues[$entry.Key],
                'Process')
        }
        $stopwatch.Stop()
    }

    $status = if ($exitCode -eq 0) { 'passed' } else { 'failed' }
    return [ordered]@{
        name = $Name
        command = $CommandLabel
        status = $status
        exitCode = $exitCode
        durationMs = $stopwatch.ElapsedMilliseconds
    }
}

if (-not $Execute) {
    Write-Host 'Dry run only. No build, tests, Redis connection, or report file will be created.'
    Write-Host 'Rerun with -Execute to run: mvn clean -DskipTests compile; focused 35-test gate; PowerShell validation script checks.'
    Write-Host 'Add -IncludeRedis only when a disposable Redis database 1 is reachable and AI_REDIS_URL is configured if needed.'
    return
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repositoryRoot 'target/ai-validation/langgraph-real-gate.json'
}

$maven = Resolve-MavenCommand
$powerShell = (Get-Process -Id $PID).Path
$steps = [System.Collections.Generic.List[object]]::new()
$originalLocation = Get-Location

try {
    Set-Location $repositoryRoot

    $steps.Add((Invoke-ValidationStep `
        -Name 'java-clean-compile' `
        -CommandLabel 'mvn clean -DskipTests compile' `
        -FilePath $maven `
        -ArgumentList @('clean', '-DskipTests', 'compile')))

    $steps.Add((Invoke-ValidationStep `
        -Name 'java-focused-tests' `
        -CommandLabel "mvn -Dtest=$focusedTests test" `
        -FilePath $maven `
        -ArgumentList @("-Dtest=$focusedTests", 'test')))

    $steps.Add((Invoke-ValidationStep `
        -Name 'powershell-validation-scripts' `
        -CommandLabel 'scripts/ai-validation-scripts.tests.ps1' `
        -FilePath $powerShell `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $PSScriptRoot 'ai-validation-scripts.tests.ps1'))))

    if ($IncludeRedis) {
        $steps.Add((Invoke-ValidationStep `
            -Name 'redis-idempotency-integration' `
            -CommandLabel 'mvn -Dtest=ToolInvocationIdempotencyRedisIT test' `
            -FilePath $maven `
            -ArgumentList @('-Dtest=ToolInvocationIdempotencyRedisIT', 'test') `
            -EnvironmentVariables @{ AI_REDIS_INTEGRATION = 'true' }))
    }
} finally {
    Set-Location $originalLocation
}

$failedSteps = @($steps | Where-Object { $_.status -ne 'passed' })
$report = [ordered]@{
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    overallStatus = if ($failedSteps.Count -eq 0) { 'passed' } else { 'failed' }
    includeRedis = [bool]$IncludeRedis
    steps = @($steps)
    manualValidationRequired = @(
        'HTML, MULTI_FILE, and VUE_PROJECT initial generation and follow-up edits',
        'stop, disconnect, model timeout, tool failure, and long-build terminal states',
        'Controller and Redis tool contention across two real Spring instances',
        'Legacy and LangGraph summary comparison plus Legacy rollback',
        'real Uvicorn/proxy backpressure and npm wrapper process cleanup'
    )
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$report | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "`nValidation summary written to $OutputPath"

if ($failedSteps.Count -gt 0) {
    exit 1
}
