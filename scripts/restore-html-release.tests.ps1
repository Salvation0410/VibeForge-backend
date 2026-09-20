Set-StrictMode -Version Latest
$scriptPath = Join-Path $PSScriptRoot 'restore-html-release.ps1'
$source = Get-Content -Raw -LiteralPath $scriptPath

if ($source -notmatch '\$PSScriptRoot') {
    throw '恢复脚本必须从 $PSScriptRoot 推导仓库根目录。'
}
if ($source -match 'Join-Path \(Get-Location\) .projects.') {
    throw '恢复脚本不得使用调用者当前目录判断 projects/。'
}
if ($source -notmatch '\[System\.IO\.Path\]::GetFullPath') {
    throw '恢复脚本必须规范化候选路径和 projects/ 边界路径。'
}

'restore-html-release path-boundary checks passed'
