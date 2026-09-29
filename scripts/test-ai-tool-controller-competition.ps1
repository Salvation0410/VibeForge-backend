param(
    [switch]$Execute,
    [string]$FirstSpringBaseUrl = 'http://localhost:8123/api',
    [string]$SecondSpringBaseUrl = 'http://localhost:8124/api',
    [string]$InternalToken = $env:AI_SERVICE_INTERNAL_BEARER_TOKEN,
    [long]$IsolatedVueAppId,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http

function Assert-Inputs {
    if (-not $Execute) { return }
    if ([string]::IsNullOrWhiteSpace($InternalToken)) { throw 'InternalToken is required.' }
    if ($IsolatedVueAppId -le 0) { throw 'IsolatedVueAppId must be positive.' }
    $first = $FirstSpringBaseUrl.TrimEnd('/').ToLowerInvariant()
    $second = $SecondSpringBaseUrl.TrimEnd('/').ToLowerInvariant()
    if ($first -eq $second) { throw 'FirstSpringBaseUrl and SecondSpringBaseUrl must be different.' }
}

function New-ToolBody(
    [long]$AppId,
    [string]$RequestId,
    [string]$ToolCallId,
    [string]$ToolName,
    [hashtable]$Arguments
) {
    return @{
        appId = $AppId
        requestId = $RequestId
        toolCallId = $ToolCallId
        toolName = $ToolName
        arguments = $Arguments
    }
}

function New-JsonRequest([string]$Uri, [string]$Json, [string]$Token) {
    $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $Uri)
    $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token)
    $request.Content = [System.Net.Http.StringContent]::new(
        $Json,
        [System.Text.Encoding]::UTF8,
        'application/json')
    return $request
}

# Return status and body for assertions; never print or persist raw response bodies.
function Invoke-Json([string]$BaseUrl, [hashtable]$Body) {
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $request = New-JsonRequest "$($BaseUrl.TrimEnd('/'))/internal/ai-tools/invoke" `
        ($Body | ConvertTo-Json -Depth 8 -Compress) $InternalToken
    $response = $null
    try {
        $responseTask = $client.SendAsync($request)
        $responseAwaiter = $responseTask.GetAwaiter()
        $response = $responseAwaiter.GetResult()
        $contentTask = $response.Content.ReadAsStringAsync()
        $contentAwaiter = $contentTask.GetAwaiter()
        $responseBody = $contentAwaiter.GetResult()
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            Body = $responseBody
        }
    } finally {
        if ($null -ne $response) { $response.Dispose() }
        $request.Dispose()
        $client.Dispose()
    }
}

# Start both requests before awaiting either result to create real Controller/Redis contention.
function Invoke-ConcurrentJson([hashtable]$Body) {
    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $json = $Body | ConvertTo-Json -Depth 8 -Compress
    $uri1 = "$($FirstSpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke"
    $uri2 = "$($SecondSpringBaseUrl.TrimEnd('/'))/internal/ai-tools/invoke"
    $request1 = New-JsonRequest $uri1 $json $InternalToken
    $request2 = New-JsonRequest $uri2 $json $InternalToken
    $response1 = $null
    $response2 = $null
    try {
        $task1 = $client.SendAsync($request1)
        $task2 = $client.SendAsync($request2)
        $allTasks = [System.Threading.Tasks.Task]::WhenAll([System.Threading.Tasks.Task[]]@($task1, $task2))
        $allAwaiter = $allTasks.GetAwaiter()
        [void]$allAwaiter.GetResult()
        $awaiter1 = $task1.GetAwaiter()
        $awaiter2 = $task2.GetAwaiter()
        $response1 = $awaiter1.GetResult()
        $response2 = $awaiter2.GetResult()
        $contentTask1 = $response1.Content.ReadAsStringAsync()
        $contentTask2 = $response2.Content.ReadAsStringAsync()
        $contentAwaiter1 = $contentTask1.GetAwaiter()
        $contentAwaiter2 = $contentTask2.GetAwaiter()
        $responseBody1 = $contentAwaiter1.GetResult()
        $responseBody2 = $contentAwaiter2.GetResult()
        return @(
            [pscustomobject]@{
                StatusCode = [int]$response1.StatusCode
                Body = $responseBody1
            },
            [pscustomobject]@{
                StatusCode = [int]$response2.StatusCode
                Body = $responseBody2
            }
        )
    } finally {
        if ($null -ne $response1) { $response1.Dispose() }
        if ($null -ne $response2) { $response2.Dispose() }
        $request1.Dispose()
        $request2.Dispose()
        $client.Dispose()
    }
}

function Assert-ToolResult($Response, [string]$Label, [bool]$ExpectedOk) {
    if ($null -eq $Response -or $Response.StatusCode -ne 200) {
        $actual = if ($null -eq $Response) { 'no-response' } else { $Response.StatusCode }
        throw "$Label expected HTTP 200 but received $actual."
    }
    $payload = $Response.Body | ConvertFrom-Json
    if ([int]$payload.code -ne 0) { throw "$Label expected business code 0 but received $($payload.code)." }
    if ([bool]$payload.data.ok -ne $ExpectedOk) {
        throw "$Label expected data.ok=$ExpectedOk but received $($payload.data.ok)."
    }
    return $payload
}

Assert-Inputs
if (-not $Execute) {
    Write-Host 'Dry run only. The real check requires two Spring instances sharing Redis and the same project root, plus an isolated Vue application ID.'
    Write-Host 'Rerun with -Execute to create and delete one random file under ai-validation/ in that isolated application.'
    return
}

$runId = [guid]::NewGuid().ToString('N')
$relativePath = "ai-validation/controller-competition-$runId.tmp"
$setupSucceeded = $false
$results = [System.Collections.Generic.List[object]]::new()

try {
    $setupBody = New-ToolBody $IsolatedVueAppId "controller-setup-$runId" 'setup-write' 'file_write' @{
        relativeFilePath = $relativePath
        content = "controller-competition-$runId"
        codeGenType = 'VUE_PROJECT'
    }
    $setup = Invoke-Json $FirstSpringBaseUrl $setupBody
    [void](Assert-ToolResult $setup 'setup write' $true)
    $setupSucceeded = $true
    $results.Add([ordered]@{ check = 'setup write'; status = $setup.StatusCode; businessCode = 0 })

    $requestId = "controller-race-$runId"
    $toolCallId = 'shared-delete'
    $deleteBody = New-ToolBody $IsolatedVueAppId $requestId $toolCallId 'file_delete' @{
        relativeFilePath = $relativePath
        codeGenType = 'VUE_PROJECT'
    }
    $race = @(Invoke-ConcurrentJson $deleteBody)
    if ($race.Count -ne 2) { throw 'concurrent delete expected two responses.' }
    [void](Assert-ToolResult $race[0] 'concurrent delete first controller' $true)
    [void](Assert-ToolResult $race[1] 'concurrent delete second controller' $true)
    $results.Add([ordered]@{
        check = 'concurrent delete'
        firstStatus = $race[0].StatusCode
        secondStatus = $race[1].StatusCode
        bothReplayedOk = $true
    })

    $replay1 = Invoke-Json $FirstSpringBaseUrl $deleteBody
    $replay2 = Invoke-Json $SecondSpringBaseUrl $deleteBody
    [void](Assert-ToolResult $replay1 'replay first controller' $true)
    [void](Assert-ToolResult $replay2 'replay second controller' $true)
    $results.Add([ordered]@{
        check = 'replay'
        firstStatus = $replay1.StatusCode
        secondStatus = $replay2.StatusCode
        bothReplayedOk = $true
    })

    $probeBody = New-ToolBody $IsolatedVueAppId "controller-probe-$runId" 'fresh-delete' 'file_delete' @{
        relativeFilePath = $relativePath
        codeGenType = 'VUE_PROJECT'
    }
    $probe = Invoke-Json $FirstSpringBaseUrl $probeBody
    [void](Assert-ToolResult $probe 'fresh-scope absence probe' $false)
    $results.Add([ordered]@{
        check = 'fresh-scope absence probe'
        status = $probe.StatusCode
        fileAlreadyAbsent = $true
    })
} finally {
    if ($setupSucceeded) {
        try {
            $cleanupBody = New-ToolBody $IsolatedVueAppId "controller-cleanup-$runId" 'cleanup-delete' 'file_delete' @{
                relativeFilePath = $relativePath
                codeGenType = 'VUE_PROJECT'
            }
            [void](Invoke-Json $FirstSpringBaseUrl $cleanupBody)
        } catch {
            Write-Warning 'Temporary validation file cleanup could not be confirmed; inspect the isolated application ai-validation directory.'
        }
    }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path (Get-Location) 'target/ai-validation/tool-controller-competition.json'
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
$results | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $OutputPath
Write-Host "Controller competition summary written to $OutputPath"
