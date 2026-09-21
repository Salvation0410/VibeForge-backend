param(
    [Parameter(Mandatory)] [Int64] $AppId,
    [Parameter(Mandatory)] [string] $CandidateFile,
    [Parameter(Mandatory)] [string] $RequestId,
    [Parameter(Mandatory)] [Microsoft.PowerShell.Commands.WebRequestSession] $WebSession,
    [string] $BaseUrl = 'http://localhost:8123/api',
    [switch] $Commit
)

$resolvedCandidate = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $CandidateFile -ErrorAction Stop).Path)
if (-not (Test-Path -LiteralPath $resolvedCandidate -PathType Leaf)) {
    throw "CandidateFile 必须是 UTF-8 HTML 文件: $CandidateFile"
}
$repositoryRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectsRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot 'projects'))
$projectsPrefix = $projectsRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if ($resolvedCandidate.StartsWith($projectsPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw '禁止从 projects/ 读取恢复候选；请提供操作员明确审核的独立文件。'
}

# 该脚本只读取操作员显式指定的候选文件，不搜索历史记录，也不读取或修改 projects/。
$candidateHtml = [System.IO.File]::ReadAllText($resolvedCandidate, [System.Text.UTF8Encoding]::new($false, $true))
$sourceDescription = "operator-reviewed file: $resolvedCandidate"
$body = @{
    appId = $AppId
    candidateHtml = $candidateHtml
    requestId = $RequestId
    sourceDescription = $sourceDescription
    dryRun = -not $Commit.IsPresent
} | ConvertTo-Json -Depth 4

$uri = $BaseUrl.TrimEnd('/') + '/apps/admin/artifacts/html/recover'
Invoke-RestMethod -Uri $uri -Method Post -WebSession $WebSession -ContentType 'application/json; charset=utf-8' -Body $body
