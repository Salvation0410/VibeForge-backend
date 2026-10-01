Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path $PSScriptRoot 'verify-customer-service-rag.ps1'
if (-not (Test-Path -LiteralPath $scriptPath)) { throw 'Missing customer service RAG validation gate.' }
$source = Get-Content -Raw -LiteralPath $scriptPath
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    $scriptPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -ne 0) { throw "Validation gate parse error: $($parseErrors[0].Message)" }

foreach ($marker in @(
    '[switch]$Execute', '[switch]$IncludeMilvus', '[switch]$IncludeCloseAi',
    '[switch]$IncludeGpu', '[switch]$IncludeOss', '[switch]$IncludeEndToEnd',
    '[string]$ReportPath', '[int]$TimeoutSeconds', 'AI_SERVICE_INTERNAL_BEARER_TOKEN',
    'HttpClientHandler', 'AllowAutoRedirect', 'ResponseHeadersRead', '65536',
    'schemaVersion', 'stableErrorCode', 'evidenceReference',
    'SAFE_ENTRYPOINT_UNAVAILABLE', 'MANUAL_REQUIRED_DESTRUCTIVE'
)) {
    if ($source -notmatch [regex]::Escape($marker)) { throw "Missing contract marker: $marker" }
}
foreach ($forbidden in @('Invoke-WebRequest', 'Invoke-RestMethod', 'Get-Content.*\.env')) {
    if ($source -match $forbidden) { throw "Forbidden HTTP or secret-loading path remains: $forbidden" }
}

Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Net;
using System.Text;
using System.Threading;

public sealed class CustomerRagGateTestServer : IDisposable {
    private readonly HttpListener listener = new HttpListener();
    private readonly Thread acceptThread;
    private volatile bool stopping;
    private int requestCount;
    private int redirectTargetCount;

    public CustomerRagGateTestServer(int port) {
        listener.Prefixes.Add("http://127.0.0.1:" + port + "/");
        acceptThread = new Thread(AcceptLoop);
        acceptThread.IsBackground = true;
    }

    public int RequestCount { get { return Volatile.Read(ref requestCount); } }
    public int RedirectTargetCount { get { return Volatile.Read(ref redirectTargetCount); } }

    public void Start() { listener.Start(); acceptThread.Start(); }

    private void AcceptLoop() {
        while (!stopping) {
            try {
                HttpListenerContext context = listener.GetContext();
                ThreadPool.QueueUserWorkItem(_ => Handle(context));
            } catch (HttpListenerException) {
                if (!stopping) throw;
            } catch (ObjectDisposedException) {
                if (!stopping) throw;
            }
        }
    }

    private static string Scenario(string path) {
        string[] parts = path.Trim('/').Split('/');
        return parts.Length == 0 ? "valid" : parts[0].ToLowerInvariant();
    }

    private static byte[] Utf8(string value) { return Encoding.UTF8.GetBytes(value); }

    private static void Write(HttpListenerContext context, int status, string body, bool chunked) {
        byte[] bytes = Utf8(body);
        context.Response.StatusCode = status;
        context.Response.StatusDescription = "Validation Test";
        context.Response.KeepAlive = false;
        context.Response.ContentType = "application/json";
        if (chunked) context.Response.SendChunked = true;
        else context.Response.ContentLength64 = bytes.Length;
        try { context.Response.OutputStream.Write(bytes, 0, bytes.Length); }
        catch (IOException) { }
        catch (HttpListenerException) { }
        finally { try { context.Response.Close(); } catch { } }
    }

    private void Handle(HttpListenerContext context) {
        Interlocked.Increment(ref requestCount);
        string path = context.Request.Url.AbsolutePath.ToLowerInvariant();
        string scenario = Scenario(path);
        if (path.EndsWith("/redirect-target")) {
            Interlocked.Increment(ref redirectTargetCount);
            Write(context, 200, "{\"status\":\"redirected\"}", false);
            return;
        }
        if (path.Contains("/spring") || path.Contains("/frontend")) {
            int status = 200;
            if (scenario == "status-redirect") status = path.Contains("/spring") ? 302 : 399;
            else if (scenario == "status-401") status = path.Contains("/spring") ? 401 : 200;
            else if (scenario == "status-404") status = path.Contains("/frontend") ? 404 : 200;
            else if (scenario == "status-500") status = 500;
            if (status >= 300 && status < 400) {
                context.Response.RedirectLocation = "http://127.0.0.1:" + context.Request.Url.Port +
                    "/redirect-target?token=location-secret";
            }
            Write(context, status, "raw-status-body-secret", false);
            return;
        }
        if (path.EndsWith("/health/live")) {
            Write(context, 200, "{\"status\":\"live\"}", false);
            return;
        }
        if (path.EndsWith("/health/ready")) {
            if (scenario == "large-length") {
                Write(context, 200, new string('x', 70000), false);
                return;
            }
            if (scenario == "large-chunked") {
                Write(context, 200, new string('x', 70000), true);
                return;
            }
            if (scenario == "slow-stream") {
                context.Response.StatusCode = 200;
                context.Response.SendChunked = true;
                context.Response.ContentType = "application/json";
                byte[] first = Utf8("{\"status\":");
                try {
                    context.Response.OutputStream.Write(first, 0, first.Length);
                    context.Response.OutputStream.Flush();
                    Thread.Sleep(2500);
                    byte[] rest = Utf8("\"ready\",\"checkpoint\":true}");
                    context.Response.OutputStream.Write(rest, 0, rest.Length);
                } catch { }
                finally { try { context.Response.Close(); } catch { } }
                return;
            }
            string ready = "{\"status\":\"ready\",\"checkpoint\":true}";
            if (scenario == "ready-string-true") ready = "{\"status\":\"ready\",\"checkpoint\":\"true\"}";
            else if (scenario == "ready-number") ready = "{\"status\":\"ready\",\"checkpoint\":1}";
            else if (scenario == "ready-string-false") ready = "{\"status\":\"ready\",\"checkpoint\":\"false\"}";
            else if (scenario == "ready-array") ready = "[]";
            else if (scenario == "ready-extra") ready = "{\"status\":\"ready\",\"checkpoint\":true,\"extra\":1}";
            Write(context, 200, ready, false);
            return;
        }
        if (path.EndsWith("/internal/v1/customer-service/health")) {
            if (scenario == "customer-redirect") {
                context.Response.RedirectLocation = "http://127.0.0.1:" + context.Request.Url.Port +
                    "/redirect-target?token=auth-location-secret";
                Write(context, 302, "auth-redirect-body-secret", false);
                return;
            }
            string dependencies = "{\"answerService\":true,\"answerModel\":true,\"embedding\":true," +
                "\"etl\":true,\"leaseValidator\":true,\"milvus\":true,\"reranker\":true}";
            string health = "{\"enabled\":true,\"status\":\"healthy\",\"reason\":\"CUSTOMER_SERVICE_READY\"," +
                "\"ready\":true,\"degraded\":false,\"dependencies\":" + dependencies + "}";
            if (scenario == "customer-string-true") health = health.Replace("\"enabled\":true", "\"enabled\":\"true\"");
            else if (scenario == "customer-number") health = health.Replace("\"ready\":true", "\"ready\":1");
            else if (scenario == "customer-string-false") health = health.Replace("\"degraded\":false", "\"degraded\":\"false\"");
            else if (scenario == "customer-array") health = "[]";
            else if (scenario == "customer-extra") health = health.Substring(0, health.Length - 1) + ",\"extra\":1}";
            else if (scenario == "customer-dependency-string") health = health.Replace("\"milvus\":true", "\"milvus\":\"true\"");
            else if (scenario == "customer-dependency-extra") health = health.Replace("\"reranker\":true", "\"reranker\":true,\"extra\":true");
            Write(context, 200, health, false);
            return;
        }
        Write(context, 404, "not-found-secret", false);
    }

    public void Dispose() {
        stopping = true;
        try { listener.Stop(); } catch { }
        try { listener.Close(); } catch { }
        if (acceptThread.IsAlive) acceptThread.Join(2000);
    }
}
"@

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try { return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

$shellPath = (Get-Process -Id $PID).Path
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("customer-rag-gate-test-" + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $tempRoot)
$server = $null
$previousPath = [Environment]::GetEnvironmentVariable('PATH', 'Process')
try {
    $externalTrap = Join-Path $tempRoot 'external-command-called.txt'
    $trapDirectory = Join-Path $tempRoot 'command-traps'
    [void](New-Item -ItemType Directory -Path $trapDirectory)
    foreach ($commandName in @('docker.cmd', 'uv.cmd', 'python.cmd')) {
        "@echo called>>`"$externalTrap`"" | Set-Content -Encoding ASCII -LiteralPath (Join-Path $trapDirectory $commandName)
    }
    [Environment]::SetEnvironmentVariable('PATH', "$trapDirectory;$previousPath", 'Process')

    $runnerPath = Join-Path $tempRoot 'run-gate.ps1'
    @'
param(
    [string]$GatePath,
    [string]$PythonBaseUrl,
    [string]$SpringBaseUrl,
    [string]$FrontendBaseUrl,
    [string]$ReportPath,
    [string]$EvidenceDirectory,
    [string]$IncludeGroups,
    [string]$UseToken,
    [int]$TimeoutSeconds
)
$ErrorActionPreference = 'Stop'
if ($UseToken -eq 'true') {
    [Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', 'synthetic-test-token', 'Process')
} else {
    [Environment]::SetEnvironmentVariable('AI_SERVICE_INTERNAL_BEARER_TOKEN', $null, 'Process')
}
$parameters = @{
    Execute = $true
    PythonBaseUrl = $PythonBaseUrl
    SpringBaseUrl = $SpringBaseUrl
    FrontendBaseUrl = $FrontendBaseUrl
    TimeoutSeconds = $TimeoutSeconds
}
if ($ReportPath -ne '__NONE__') { $parameters.ReportPath = $ReportPath }
if ($EvidenceDirectory -ne '__NONE__') { $parameters.EvidenceDirectory = $EvidenceDirectory }
if ($IncludeGroups -ne '__NONE__') {
    foreach ($group in @($IncludeGroups -split ',' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $parameters[$group] = $true
    }
}
try {
    & $GatePath @parameters
    exit $LASTEXITCODE
} catch {
    Write-Error ("RUNNER_ERROR: " + $_.Exception.Message + " STACK: " + $_.ScriptStackTrace)
    exit 1
}
'@ | Set-Content -Encoding UTF8 -LiteralPath $runnerPath

    $port = Get-FreePort
    $server = [CustomerRagGateTestServer]::new($port)
    $server.Start()

    function Invoke-Scenario {
        param(
            [string]$Name,
            [string]$Scenario = 'valid',
            [bool]$UseToken = $false,
            [string[]]$IncludeGroups = @(),
            [int]$TimeoutSeconds = 2
        )
        $reportPath = Join-Path $tempRoot "$Name.json"
        $base = "http://127.0.0.1:$port/$Scenario"
        $includeArgument = if ($IncludeGroups.Count -eq 0) { '__NONE__' } else { $IncludeGroups -join ',' }
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $output = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $runnerPath `
            $scriptPath "$base/python" "$base/spring" "$base/frontend" $reportPath '__NONE__' `
            $includeArgument $UseToken.ToString().ToLowerInvariant() $TimeoutSeconds 2>&1
        $exitCode = $LASTEXITCODE
        $ErrorActionPreference = $previousErrorActionPreference
        if (-not (Test-Path -LiteralPath $reportPath)) {
            throw "Scenario $Name did not create a report: $($output | Out-String)"
        }
        $raw = Get-Content -Raw -LiteralPath $reportPath
        foreach ($forbidden in @(
            'synthetic-test-token', 'raw-status-body-secret', 'location-secret',
            'auth-location-secret', 'auth-redirect-body-secret', 'not-found-secret'
        )) {
            if ($raw -match [regex]::Escape($forbidden)) { throw "Scenario $Name leaked $forbidden" }
        }
        return [pscustomobject]@{
            ExitCode = $exitCode
            Report = ($raw | ConvertFrom-Json)
            Raw = $raw
            Output = ($output | Out-String)
        }
    }

    $dryOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $scriptPath 2>&1
    if ($LASTEXITCODE -ne 0 -or "$dryOutput" -notmatch 'Dry run only') { throw 'Default dry-run failed.' }
    if (Test-Path -LiteralPath $externalTrap) { throw 'Dry-run invoked an external command.' }
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $invalidOptIn = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $scriptPath -IncludeMilvus 2>&1
    $invalidOptInExit = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($invalidOptInExit -ne 1 -or "$invalidOptIn" -notmatch 'OPT_IN_REQUIRES_EXECUTE') {
        throw 'Opt-in without Execute must fail before external work.'
    }

    $valid = Invoke-Scenario -Name 'valid' -UseToken $true
    if ($valid.ExitCode -ne 0) {
        $nonPassing = @($valid.Report.steps | Where-Object { $_.status -ne 'passed' -and $_.status -ne 'not-run' } |
            ForEach-Object { "$($_.name)=$($_.status)/$($_.stableErrorCode)" }) -join ', '
        throw "All passed/not-run must exit 0, got $($valid.ExitCode): $nonPassing; $($valid.Output)"
    }
    $expectedTopLevel = @('environment', 'generatedAt', 'schemaVersion', 'steps')
    if (@(Compare-Object $expectedTopLevel @($valid.Report.PSObject.Properties.Name | Sort-Object)).Count -ne 0) {
        throw 'Report top-level whitelist changed.'
    }
    $expectedStepFields = @(
        'chunkCount', 'documentId', 'documentVersion', 'durationMs',
        'evidenceReference', 'exitCode', 'name', 'stableErrorCode', 'status')
    foreach ($step in $valid.Report.steps) {
        if (@(Compare-Object $expectedStepFields @($step.PSObject.Properties.Name | Sort-Object)).Count -ne 0) {
            throw "Step whitelist changed for $($step.name)."
        }
    }
    if ($valid.Raw -match '(?i)https?://|\?|authorization|cookie|bearer\s|signed[_-]?url') {
        throw 'Report contains a URL, query, or credential marker.'
    }
    $missingToken = Invoke-Scenario -Name 'missing-token'
    if ($missingToken.ExitCode -ne 2) { throw 'Blocked-only execution must exit 2.' }

    foreach ($case in @(
        @{ name = 'status-redirect'; expectedExit = 2 },
        @{ name = 'status-401'; expectedExit = 1 },
        @{ name = 'status-404'; expectedExit = 1 },
        @{ name = 'status-500'; expectedExit = 1 }
    )) {
        $result = Invoke-Scenario -Name $case.name -Scenario $case.name
        if ($result.ExitCode -ne $case.expectedExit) {
            throw "$($case.name) expected exit $($case.expectedExit), got $($result.ExitCode)."
        }
        foreach ($stepName in @('spring-http-prerequisite', 'frontend-http-prerequisite')) {
            $step = @($result.Report.steps | Where-Object { $_.name -eq $stepName })[0]
            $shouldPass = $case.name -eq 'status-redirect' -or
                ($case.name -eq 'status-401' -and $stepName -eq 'frontend-http-prerequisite') -or
                ($case.name -eq 'status-404' -and $stepName -eq 'spring-http-prerequisite')
            if ($shouldPass) {
                if ($step.status -ne 'passed') { throw "$($case.name) must pass $stepName." }
            } elseif ($step.status -ne 'failed' -or $step.stableErrorCode -ne 'HTTP_STATUS_UNEXPECTED') {
                throw "$($case.name) must fail $stepName with HTTP_STATUS_UNEXPECTED."
            }
        }
    }
    if ($server.RedirectTargetCount -ne 0) { throw 'Status redirects must not be followed.' }

    foreach ($scenarioName in @(
        'ready-string-true', 'ready-number', 'ready-string-false', 'ready-array', 'ready-extra'
    )) {
        $result = Invoke-Scenario -Name $scenarioName -Scenario $scenarioName
        $step = @($result.Report.steps | Where-Object { $_.name -eq 'python-health-ready' })[0]
        if ($result.ExitCode -ne 1 -or $step.status -ne 'failed' -or
            $step.stableErrorCode -ne 'HEALTH_RESPONSE_INVALID') {
            throw "$scenarioName must fail strict ready JSON validation."
        }
    }

    foreach ($scenarioName in @(
        'customer-string-true', 'customer-number', 'customer-string-false',
        'customer-array', 'customer-extra', 'customer-dependency-string',
        'customer-dependency-extra'
    )) {
        $result = Invoke-Scenario -Name $scenarioName -Scenario $scenarioName -UseToken $true
        $step = @($result.Report.steps | Where-Object { $_.name -eq 'authenticated-customer-service-health' })[0]
        if ($result.ExitCode -ne 1 -or $step.status -ne 'failed' -or
            $step.stableErrorCode -ne 'HEALTH_RESPONSE_INVALID') {
            throw "$scenarioName must fail strict customer health JSON validation."
        }
    }

    $beforeRedirect = $server.RedirectTargetCount
    $authRedirect = Invoke-Scenario -Name 'customer-redirect' -Scenario 'customer-redirect' -UseToken $true
    if ($authRedirect.ExitCode -ne 1 -or $server.RedirectTargetCount -ne $beforeRedirect) {
        throw 'Authenticated health redirect must fail without forwarding Authorization.'
    }

    foreach ($scenarioName in @('large-length', 'large-chunked')) {
        $result = Invoke-Scenario -Name $scenarioName -Scenario $scenarioName
        $step = @($result.Report.steps | Where-Object { $_.name -eq 'python-health-ready' })[0]
        if ($result.ExitCode -ne 1 -or $step.status -ne 'failed' -or
            $step.stableErrorCode -ne 'HTTP_RESPONSE_TOO_LARGE') {
            throw "$scenarioName must fail at the 64 KiB response limit."
        }
    }
    $slow = Invoke-Scenario -Name 'slow-stream' -Scenario 'slow-stream' -TimeoutSeconds 1
    $slowStep = @($slow.Report.steps | Where-Object { $_.name -eq 'python-health-ready' })[0]
    if ($slow.ExitCode -notin @(1, 2) -or $slowStep.stableErrorCode -ne 'HTTP_TIMEOUT') {
        throw "Slow stream must map to stable HTTP_TIMEOUT; got $($slow.ExitCode)/$($slowStep.status)/$($slowStep.stableErrorCode)."
    }

    $groupDefinitions = [ordered]@{
        IncludeMilvus = @{ prefix = 'milvus-'; destructive = @('milvus-restart-recovery') }
        IncludeCloseAi = @{ prefix = 'closeai-'; destructive = @() }
        IncludeGpu = @{ prefix = 'gpu-'; destructive = @('gpu-oom-mapping') }
        IncludeOss = @{ prefix = 'oss-'; destructive = @('oss-delete') }
        IncludeEndToEnd = @{ prefix = 'e2e-'; destructive = @('e2e-dependency-outage') }
    }
    $optInCases = @()
    foreach ($key in $groupDefinitions.Keys) { $optInCases += ,@($key) }
    $optInCases += ,@($groupDefinitions.Keys)
    $index = 0
    foreach ($selected in $optInCases) {
        $index++
        $result = Invoke-Scenario -Name "opt-in-$index" -UseToken $true -IncludeGroups $selected
        if ($result.ExitCode -ne 2) { throw "Opt-in case $index must exit 2." }
        foreach ($key in $groupDefinitions.Keys) {
            $definition = $groupDefinitions[$key]
            foreach ($step in @($result.Report.steps | Where-Object { $_.name.StartsWith($definition.prefix) })) {
                if ($selected -notcontains $key) {
                    if ($step.status -ne 'not-run' -or $step.stableErrorCode -ne 'OPT_IN_REQUIRED') {
                        throw "$key must remain OPT_IN_REQUIRED."
                    }
                } elseif ($definition.destructive -contains $step.name) {
                    if ($step.status -ne 'blocked' -or $step.stableErrorCode -ne 'MANUAL_REQUIRED_DESTRUCTIVE') {
                        throw "$($step.name) must remain manual-required."
                    }
                } elseif ($step.status -ne 'blocked' -or $step.stableErrorCode -ne 'SAFE_ENTRYPOINT_UNAVAILABLE') {
                    throw "$($step.name) must remain blocked without a safe entrypoint."
                }
            }
        }
    }
    if (Test-Path -LiteralPath $externalTrap) { throw 'An opt-in invoked a real external command.' }

    $requestCount = $server.RequestCount
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $unsafeUrls = @(
        'http://example.com/api', 'http://localhost.evil/api',
        'http://user@localhost/api', 'http://2130706433/api', 'http://127.1/api')
    foreach ($unsafeUrl in $unsafeUrls) {
        $remoteHttp = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
            -SpringBaseUrl $unsafeUrl 2>&1
        $remoteHttpExit = $LASTEXITCODE
        $normalized = ($remoteHttp | Out-String) -replace '\s+', ' '
        if ($remoteHttpExit -ne 1 -or $normalized -notmatch 'REMOTE_HTTP_NOT_ALLOWED|URL_POLICY_INVALID') {
            throw "Unsafe HTTP base URL was accepted: $unsafeUrl"
        }
    }
    $remoteHttps = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $scriptPath `
        -SpringBaseUrl 'https://example.com/api' 2>&1
    $remoteHttpsExit = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($remoteHttpsExit -ne 0 -or "$remoteHttps" -notmatch 'Dry run only') {
        throw 'Remote HTTPS base URL must validate during dry-run without network access.'
    }
    if ($server.RequestCount -ne $requestCount) { throw 'URL validation unexpectedly sent a request.' }

    $evidenceWrapper = Join-Path $tempRoot 'evidence-reference.ps1'
    @'
param([string]$GatePath)
$ErrorActionPreference = 'Stop'
. $GatePath | Out-Null
[void](New-StepResult -Name 'valid' -Status 'passed' -ExitCode 0 -EvidenceReference 'manual/group/item')
foreach ($value in @(
    'C:/secret', 'C:\secret', '\\server\share', '/rooted', './item', '../item',
    'manual/./item', 'manual/../item', 'manual\item', 'manual:item',
    'http://example.com/item', 'manual/item?query=1', 'manual/%2e%2e/item', 'manual/%2fitem'
)) {
    $rejected = $false
    try { [void](New-StepResult -Name 'invalid' -Status 'failed' -ExitCode 1 -EvidenceReference $value) }
    catch { $rejected = $true }
    if (-not $rejected) { throw "Unsafe evidence reference accepted: $value" }
}
'@ | Set-Content -Encoding UTF8 -LiteralPath $evidenceWrapper
    & $shellPath -NoProfile -ExecutionPolicy Bypass -File $evidenceWrapper $scriptPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Evidence reference validation failed.' }

    $existingPath = Join-Path $tempRoot 'existing.json'
    'original-evidence' | Set-Content -NoNewline -Encoding ASCII -LiteralPath $existingPath
    $beforeExisting = $server.RequestCount
    $base = "http://127.0.0.1:$port/valid"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $existingOutput = & $shellPath -NoProfile -ExecutionPolicy Bypass -File $runnerPath `
        $scriptPath "$base/python" "$base/spring" "$base/frontend" $existingPath '__NONE__' '__NONE__' true 2 2>&1
    $existingExit = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    if ($existingExit -ne 1 -or (Get-Content -Raw $existingPath) -ne 'original-evidence' -or
        $server.RequestCount -ne $beforeExisting -or
        (($existingOutput | Out-String) -replace '\s+', ' ') -notmatch 'REPORT_PATH_EXISTS') {
        throw 'Existing explicit report must be rejected unchanged before network access.'
    }

    $concurrentDirectory = Join-Path $tempRoot 'concurrent'
    [void](New-Item -ItemType Directory -Path $concurrentDirectory)
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runnerPath,
        $scriptPath, "$base/python", "$base/spring", "$base/frontend", '__NONE__',
        $concurrentDirectory, '__NONE__', 'true', '2'
    )
    $first = Start-Process -FilePath $shellPath -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $second = Start-Process -FilePath $shellPath -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $first.WaitForExit()
    $second.WaitForExit()
    if ($first.ExitCode -ne 0 -or $second.ExitCode -ne 0) { throw 'Concurrent default report executions failed.' }
    $reports = @(Get-ChildItem -LiteralPath $concurrentDirectory -Filter '*.json')
    if ($reports.Count -ne 2 -or $reports[0].Name -eq $reports[1].Name) {
        throw 'Concurrent default executions must create two unique reports.'
    }
    foreach ($file in $reports) { [void]((Get-Content -Raw $file.FullName) | ConvertFrom-Json) }
    if (@(Get-ChildItem -LiteralPath $concurrentDirectory -Filter '*.tmp').Count -ne 0) {
        throw 'Atomic report writer left temporary files.'
    }
} finally {
    [Environment]::SetEnvironmentVariable('PATH', $previousPath, 'Process')
    if ($null -ne $server) { $server.Dispose() }
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "customer-service-rag validation contracts passed on PowerShell $($PSVersionTable.PSVersion)"
