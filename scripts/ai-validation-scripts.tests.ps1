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

$comparison = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'compare-ai-generation-engines.ps1')
foreach ($field in @('engine','appId','codeGenType','requestId','terminalStatus','toolNames','artifactHashes','buildStatus','errorCode','durationMs')) {
    if ($comparison -notmatch [regex]::Escape($field)) { throw "Comparison report misses $field" }
}
foreach ($forbidden in @('prompt =','source =','cookie =','token =','toolArguments =')) {
    if ($comparison -match [regex]::Escape($forbidden)) { throw "Comparison report contains forbidden field $forbidden" }
}

$e2e = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'test-ai-phase-two-e2e.ps1')
foreach ($marker in @('HTML','MULTI_FILE','VUE_PROJECT','business-error','manualChecks','RunCancellationScenario','RunLegacyRollbackScenario')) {
    if ($e2e -notmatch [regex]::Escape($marker)) { throw "E2E script misses $marker" }
}

'AI validation script static checks passed'
