param(
    [switch]$Execute,
    [string]$EnvironmentName = 'local',
    [string]$SpringBaseUrl = 'http://localhost:8123/api',
    [string]$PythonBaseUrl = 'http://localhost:8000',
    [string]$FrontendBaseUrl = 'http://localhost:5173',
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot

function New-ValidationScenario(
    [string]$ScenarioId,
    [string]$Category,
    [string]$CodeGenType,
    [string]$Phase,
    [string]$ExpectedOutcome
) {
    return [ordered]@{
        scenarioId = $ScenarioId
        priority = 'P0'
        category = $Category
        codeGenType = if ([string]::IsNullOrWhiteSpace($CodeGenType)) { $null } else { $CodeGenType }
        phase = $Phase
        expectedOutcome = $ExpectedOutcome
        engine = $null
        appId = $null
        requestId = $null
        status = 'pending'
        terminalStatus = $null
        errorCode = $null
        durationMs = $null
        oldPreviewPreserved = $null
        previewRefreshCount = $null
        historyReloaded = $null
        evidenceRefs = @()
        notes = $null
    }
}

if (-not $Execute) {
    Write-Host 'Dry run only. No validation record will be created.'
    Write-Host 'Rerun with -Execute to create a 13-scenario P0 record under target/ai-validation.'
    return
}

if ([string]::IsNullOrWhiteSpace($EnvironmentName)) {
    throw 'EnvironmentName must not be empty.'
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $repositoryRoot 'target/ai-validation/manual-validation-record.json'
}

$scenarios = @(
    (New-ValidationScenario 'html-initial' 'generation' 'HTML' 'initial' 'Complete publish, history reload, and one preview refresh'),
    (New-ValidationScenario 'html-follow-up' 'generation' 'HTML' 'follow-up' 'Requested edit preserves existing content, media, behavior, and active version safety'),
    (New-ValidationScenario 'multi-file-initial' 'generation' 'MULTI_FILE' 'initial' 'All three required files publish and the preview works'),
    (New-ValidationScenario 'multi-file-follow-up' 'generation' 'MULTI_FILE' 'follow-up' 'Requested edit preserves all unrelated files and behavior'),
    (New-ValidationScenario 'vue-initial' 'generation' 'VUE_PROJECT' 'initial' 'Tool loop, validation, build, history reload, and preview complete'),
    (New-ValidationScenario 'vue-follow-up' 'generation' 'VUE_PROJECT' 'follow-up' 'Requested edit preserves existing components, media, and interactions'),
    (New-ValidationScenario 'stop-generation' 'cancellation' '' 'stop' 'Cancellation wins before publish and the old preview remains active'),
    (New-ValidationScenario 'disconnect-stream' 'cancellation' '' 'disconnect' 'Disconnected client cannot trigger a late publish or preview refresh'),
    (New-ValidationScenario 'model-timeout' 'failure' '' 'model-timeout' 'Exactly one stable failed terminal state and the old preview remains active'),
    (New-ValidationScenario 'spring-tool-failure' 'failure' '' 'tool-failure' 'Stable business error, no unsafe retry, and no new active version'),
    (New-ValidationScenario 'long-build-cancel' 'failure' 'VUE_PROJECT' 'long-build' 'Build process tree is released and the old preview remains active'),
    (New-ValidationScenario 'dual-spring-tool-contention' 'competition' 'VUE_PROJECT' 'two-spring' 'One side effect, shared replay result, and isolated fresh scope'),
    (New-ValidationScenario 'legacy-rollback' 'rollback' '' 'legacy' 'Stable route, successful switch to Legacy, and compatible cancellation semantics')
)

$record = [ordered]@{
    recordVersion = 1
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    runId = "manual-$([guid]::NewGuid().ToString('N'))"
    environment = [ordered]@{
        name = $EnvironmentName
        springBaseUrl = $SpringBaseUrl
        pythonBaseUrl = $PythonBaseUrl
        frontendBaseUrl = $FrontendBaseUrl
    }
    allowedStatuses = @('pending', 'passed', 'failed', 'blocked', 'not-run')
    automatedEvidenceFiles = @(
        'target/ai-validation/langgraph-real-gate.json',
        'target/ai-validation/test-ai-phase-two-e2e.json',
        'target/ai-validation/tool-controller-competition.json',
        'target/ai-validation/compare-ai-generation-engines.json'
    )
    scenarios = $scenarios
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}
$record | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "Manual validation record written to $OutputPath"
