Set-StrictMode -Version Latest

# 为真实 AI 验收创建受控登录会话：优先复用调用方提供的 Cookie，否则在同一会话中完成验证码登录。
function New-AiValidationAuthenticatedSession {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [string]$Account,
        [string]$Password,
        [string]$SessionCookie,
        [string]$CaptchaCode
    )

    $baseUri = [Uri]$BaseUrl.TrimEnd('/')
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    if (-not [string]::IsNullOrWhiteSpace($SessionCookie)) {
        try {
            $session.Cookies.SetCookies($baseUri, $SessionCookie)
            $current = Invoke-WebRequest -Uri "$($baseUri.AbsoluteUri.TrimEnd('/'))/users/login" -Method Get -WebSession $session
            $currentPayload = $current.Content | ConvertFrom-Json
            if ([int]$currentPayload.code -ne 0) { throw '会话 Cookie 已失效。' }
            return $session
        } catch {
            throw '无法使用提供的会话 Cookie 完成认证。'
        }
    }

    if ([string]::IsNullOrWhiteSpace($Account) -or [string]::IsNullOrWhiteSpace($Password)) {
        throw '未提供 SessionCookie 时必须提供账号和密码。'
    }

    $captchaPath = $null
    try {
        $captcha = Invoke-WebRequest -Uri "$($baseUri.AbsoluteUri.TrimEnd('/'))/users/login/captcha" -Method Get -WebSession $session
        $captchaPayload = $captcha.Content | ConvertFrom-Json
        if ([int]$captchaPayload.code -ne 0 -or [string]::IsNullOrWhiteSpace([string]$captchaPayload.data.captchaImage)) {
            throw '无法获取登录验证码。'
        }
        $dataUrl = [string]$captchaPayload.data.captchaImage
        if ($dataUrl -notmatch '^data:image/[^;]+;base64,(.+)$') { throw '登录验证码不是有效的图片数据。' }
        $captchaPath = Join-Path ([IO.Path]::GetTempPath()) "yu-ai-login-captcha-$([guid]::NewGuid().ToString('N')).png"
        [IO.File]::WriteAllBytes($captchaPath, [Convert]::FromBase64String($Matches[1]))

        $resolvedCaptchaCode = $CaptchaCode
        if ([string]::IsNullOrWhiteSpace($resolvedCaptchaCode)) {
            try { Invoke-Item -LiteralPath $captchaPath | Out-Null }
            catch { Write-Host "验证码图片已保存到临时路径：$captchaPath" }
            $resolvedCaptchaCode = Read-Host '请输入登录验证码'
        }
        if ([string]::IsNullOrWhiteSpace($resolvedCaptchaCode)) { throw '登录验证码不能为空。' }

        $body = @{
            account = $Account
            password = $Password
            captchaCode = $resolvedCaptchaCode
        } | ConvertTo-Json
        $login = Invoke-WebRequest -Uri "$($baseUri.AbsoluteUri.TrimEnd('/'))/users/login" -Method Post `
            -WebSession $session -ContentType 'application/json; charset=utf-8' -Body $body
        $loginPayload = $login.Content | ConvertFrom-Json
        if ([int]$loginPayload.code -ne 0) { throw '验证码登录失败。' }
        return $session
    } finally {
        if (-not [string]::IsNullOrWhiteSpace($captchaPath) -and (Test-Path -LiteralPath $captchaPath)) {
            Remove-Item -LiteralPath $captchaPath -Force -ErrorAction SilentlyContinue
        }
    }
}
