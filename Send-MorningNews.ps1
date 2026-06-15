param(
    [switch]$ValidateOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvPath = Join-Path $ProjectDir ".env"
$SettingsPath = Join-Path $ProjectDir "settings.json"
$LogDir = Join-Path $ProjectDir "logs"
$LogPath = Join-Path $LogDir "morning-news.log"

Add-Type @"
using System;
using System.Net;

public class TimeoutWebClient : WebClient
{
    public int TimeoutMilliseconds { get; set; }

    protected override WebRequest GetWebRequest(Uri address)
    {
        WebRequest request = base.GetWebRequest(address);
        request.Timeout = TimeoutMilliseconds;
        return request;
    }
}
"@

function Write-AppLog {
    param(
        [string]$Message,
        [string]$Level = "INFO"
    )

    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value "[$Timestamp] [$Level] $Message"
}

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $Line = $_.Trim()
        if (-not $Line -or $Line.StartsWith("#") -or -not $Line.Contains("=")) {
            return
        }

        $Key, $Value = $Line.Split("=", 2)
        $Key = $Key.Trim()
        $Value = $Value.Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($Key, $Value, "Process")
    }
}

function Set-EnvValue {
    param(
        [string]$Name,
        [AllowNull()]$Value
    )

    if ($null -eq $Value) {
        return
    }

    [Environment]::SetEnvironmentVariable($Name, [string]$Value, "Process")
}

function Import-JsonSettings {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    $Settings = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json

    Set-EnvValue -Name "NEWS_QUERY" -Value $Settings.news_query
    Set-EnvValue -Name "NEWS_LIMIT" -Value $Settings.news_limit
    Set-EnvValue -Name "RECIPIENT_EMAIL" -Value $Settings.recipient_email
    Set-EnvValue -Name "BLOG_ENABLED" -Value $Settings.blog_enabled
    Set-EnvValue -Name "BLOG_PICK_INDEX" -Value $Settings.blog_pick_index
    Set-EnvValue -Name "BLOG_DRAFT_DIR" -Value $Settings.blog_draft_dir
    Set-EnvValue -Name "RETRY_COUNT" -Value $Settings.retry_count
    Set-EnvValue -Name "RETRY_DELAY_SECONDS" -Value $Settings.retry_delay_seconds
    Set-EnvValue -Name "REQUEST_TIMEOUT_SECONDS" -Value $Settings.request_timeout_seconds

    if ($Settings.notification_channel -eq "none") {
        Set-EnvValue -Name "ERROR_EMAIL_ENABLED" -Value "false"
    }
    else {
        Set-EnvValue -Name "ERROR_EMAIL_ENABLED" -Value $Settings.error_email_enabled
    }
}

function Get-RequiredEnv {
    param([string]$Name)

    $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($Value)) {
        throw ".env 파일에 $Name 값을 입력해 주세요."
    }

    return $Value.Trim()
}

function Get-EnvInt {
    param(
        [string]$Name,
        [int]$DefaultValue
    )

    $Value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $DefaultValue
    }

    return [int]$Value
}

function Invoke-WithRetry {
    param(
        [scriptblock]$Action,
        [string]$Name,
        [int]$RetryCount,
        [int]$DelaySeconds
    )

    for ($Attempt = 1; $Attempt -le ($RetryCount + 1); $Attempt++) {
        try {
            Write-AppLog "$Name attempt $Attempt"
            return & $Action
        }
        catch {
            Write-AppLog "${Name} failed on attempt ${Attempt}: $($_.Exception.Message)" "WARN"
            if ($Attempt -gt $RetryCount) {
                throw
            }

            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Get-NewsFeedUrl {
    $FeedUrl = [Environment]::GetEnvironmentVariable("NEWS_FEED_URL", "Process")
    if (-not [string]::IsNullOrWhiteSpace($FeedUrl)) {
        return $FeedUrl.Trim()
    }

    $Query = [Environment]::GetEnvironmentVariable("NEWS_QUERY", "Process")
    if (-not [string]::IsNullOrWhiteSpace($Query)) {
        $EncodedQuery = [uri]::EscapeDataString($Query.Trim())
        return "https://news.google.com/rss/search?q=$EncodedQuery&hl=ko&gl=KR&ceid=KR:ko"
    }

    return "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"
}

function Get-MorningNews {
    param(
        [string]$FeedUrl,
        [int]$Limit
    )

    $TimeoutSeconds = Get-EnvInt -Name "REQUEST_TIMEOUT_SECONDS" -DefaultValue 10
    $RetryCount = Get-EnvInt -Name "RETRY_COUNT" -DefaultValue 3
    $RetryDelaySeconds = Get-EnvInt -Name "RETRY_DELAY_SECONDS" -DefaultValue 3

    $Client = [TimeoutWebClient]::new()
    $Client.TimeoutMilliseconds = $TimeoutSeconds * 1000
    $Client.Encoding = [System.Text.Encoding]::UTF8
    $Client.Headers.Add("User-Agent", "MorningNewsMailer/1.0")

    try {
        [xml]$Feed = Invoke-WithRetry -Name "Download news feed" -RetryCount $RetryCount -DelaySeconds $RetryDelaySeconds -Action {
            $Client.DownloadString($FeedUrl)
        }
    }
    finally {
        $Client.Dispose()
    }

    $Items = @($Feed.rss.channel.item | Select-Object -First $Limit | ForEach-Object {
        $Source = [string]$_.source
        if ([string]::IsNullOrWhiteSpace($Source)) {
            $Source = "Google News"
        }

        [pscustomobject]@{
            Title = [string]$_.title
            Link = [string]$_.link
            Source = $Source
            Published = [string]$_.pubDate
        }
    })
    if ($Items.Count -eq 0) {
        Write-AppLog "No news items found. Skipped without error." "WARN"
        return @()
    }

    return $Items
}

function New-NewsHtml {
    param(
        $Items,
        [AllowNull()]$BlogDraft
    )

    $Today = Get-Date -Format "yyyy-MM-dd"
    $Rows = foreach ($Item in $Items) {
        $Title = [System.Net.WebUtility]::HtmlEncode([string]$Item.Title)
        $Link = [System.Net.WebUtility]::HtmlEncode([string]$Item.Link)
        $Source = [System.Net.WebUtility]::HtmlEncode([string]$Item.Source)
        if ([string]::IsNullOrWhiteSpace($Source)) {
            $Source = "Google News"
        }

        @"
<li style="margin:0 0 18px 0;">
  <a href="$Link" style="font-size:17px;font-weight:700;color:#155eef;text-decoration:none;">$Title</a>
  <div style="margin-top:6px;color:#555;font-size:13px;">$Source</div>
</li>
"@
    }

    return @"
<!doctype html>
<html lang="ko">
<body style="margin:0;padding:24px;background:#f6f7f9;font-family:Arial,'Malgun Gothic',sans-serif;">
  <main style="max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:28px;">
    <h1 style="margin:0 0 8px 0;font-size:24px;color:#111827;">아침 주요뉴스</h1>
    <p style="margin:0 0 24px 0;color:#6b7280;">$Today 기준 주요뉴스 $($Items.Count)개입니다.</p>
    <ol style="padding-left:22px;margin:0;">
      $($Rows -join "`n")
    </ol>
    $(if ($BlogDraft) {
        @"
    <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0;">
    <h2 style="margin:0 0 12px 0;font-size:20px;color:#111827;">오늘의 블로그 초안</h2>
    <p style="margin:0 0 10px 0;color:#555;font-size:14px;">선택 뉴스: $([System.Net.WebUtility]::HtmlEncode($BlogDraft.NewsTitle))</p>
    <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;border-radius:8px;padding:16px;font-family:Arial,'Malgun Gothic',sans-serif;font-size:14px;line-height:1.6;color:#111827;">$([System.Net.WebUtility]::HtmlEncode($BlogDraft.Content))</pre>
"@
    })
  </main>
</body>
</html>
"@
}

function Test-Enabled {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    return @("1", "true", "yes", "y", "on") -contains $Value.Trim().ToLowerInvariant()
}

function Convert-ToSafeFileName {
    param([string]$Text)

    $SafeText = $Text -replace '[\\/:*?"<>|]', ''
    $SafeText = $SafeText -replace '\s+', '-'
    if ($SafeText.Length -gt 60) {
        $SafeText = $SafeText.Substring(0, 60)
    }

    return $SafeText.Trim("-")
}

function New-BlogDraft {
    param(
        $Items,
        [string]$ProjectDir
    )

    $Enabled = Test-Enabled -Value ([Environment]::GetEnvironmentVariable("BLOG_ENABLED", "Process"))
    if (-not $Enabled) {
        return $null
    }

    $PickText = [Environment]::GetEnvironmentVariable("BLOG_PICK_INDEX", "Process")
    if ([string]::IsNullOrWhiteSpace($PickText)) {
        $PickIndex = 1
    }
    else {
        $PickIndex = [int]$PickText
    }

    if ($PickIndex -lt 1 -or $PickIndex -gt $Items.Count) {
        $PickIndex = 1
    }

    $Item = @($Items)[$PickIndex - 1]
    $Title = [string]$Item.Title
    $Link = [string]$Item.Link
    $Source = [string]$Item.Source
    if ([string]::IsNullOrWhiteSpace($Source)) {
        $Source = "Google News"
    }

    $Today = Get-Date -Format "yyyy-MM-dd"
    $DraftTitle = "[초안] $Title"
    $Content = @"
# $DraftTitle

## 한 줄 요약
$Title

## 도입
오늘 눈에 띈 뉴스는 "$Title"입니다. 이 이슈는 단순한 사건 소개를 넘어, 앞으로의 흐름을 살펴볼 만한 소재입니다.

## 핵심 내용
- 뉴스 출처: $Source
- 원문 링크: $Link
- 확인 날짜: $Today

## 블로그 본문 초안
이번 뉴스에서 가장 먼저 볼 부분은 이 일이 왜 지금 주목받는가입니다. 제목만 놓고 보면 하나의 사건처럼 보이지만, 독자 입장에서는 배경과 영향, 앞으로의 변화 가능성을 함께 이해하는 것이 중요합니다.

첫째, 이 뉴스는 현재 사회적 관심이 어디로 향하고 있는지를 보여줍니다. 관련 업계나 일반 소비자에게 어떤 변화가 생길 수 있는지 정리하면 글의 설득력이 높아집니다.

둘째, 단순 전달보다 해석이 필요합니다. 원문 내용을 확인한 뒤 숫자, 발언, 일정처럼 검증 가능한 정보를 보강하면 더 신뢰도 있는 블로그 글이 됩니다.

셋째, 독자에게 남길 질문을 준비하면 좋습니다. 이 변화가 내 생활이나 일에 어떤 영향을 줄지, 앞으로 무엇을 지켜봐야 할지 제안하면 글이 자연스럽게 마무리됩니다.

## 마무리
이 뉴스는 앞으로의 흐름을 살펴볼 좋은 출발점입니다. 원문을 확인한 뒤 구체적인 사실을 더하면 바로 게시 가능한 글로 다듬을 수 있습니다.
"@

    $DraftDirName = [Environment]::GetEnvironmentVariable("BLOG_DRAFT_DIR", "Process")
    if ([string]::IsNullOrWhiteSpace($DraftDirName)) {
        $DraftDirName = "blog_drafts"
    }

    $DraftDir = Join-Path $ProjectDir $DraftDirName
    New-Item -ItemType Directory -Path $DraftDir -Force | Out-Null

    $SafeName = Convert-ToSafeFileName -Text $Title
    if ([string]::IsNullOrWhiteSpace($SafeName)) {
        $SafeName = "news-blog-draft"
    }

    $DraftPath = Join-Path $DraftDir "$Today-$SafeName.md"
    Set-Content -LiteralPath $DraftPath -Value $Content -Encoding UTF8

    return [pscustomobject]@{
        NewsTitle = $Title
        NewsLink = $Link
        Path = $DraftPath
        Content = $Content
    }
}

function Send-NewsMail {
    param(
        [string]$Sender,
        [string]$Password,
        [string]$Recipient,
        [string]$Subject,
        [string]$HtmlBody
    )

    $Message = [System.Net.Mail.MailMessage]::new()
    $Message.From = $Sender
    $Message.To.Add($Recipient)
    $Message.Subject = $Subject
    $Message.SubjectEncoding = [System.Text.Encoding]::UTF8
    $Message.Body = $HtmlBody
    $Message.BodyEncoding = [System.Text.Encoding]::UTF8
    $Message.IsBodyHtml = $true

    $Smtp = [System.Net.Mail.SmtpClient]::new("smtp.gmail.com", 587)
    $Smtp.EnableSsl = $true
    $Smtp.Credentials = [System.Net.NetworkCredential]::new($Sender, $Password)

    try {
        $Smtp.Send($Message)
    }
    finally {
        $Message.Dispose()
        $Smtp.Dispose()
    }
}

function Send-ErrorAlert {
    param([string]$ErrorMessage)

    $Enabled = Test-Enabled -Value ([Environment]::GetEnvironmentVariable("ERROR_EMAIL_ENABLED", "Process"))
    if (-not $Enabled) {
        return
    }

    try {
        $Sender = Get-RequiredEnv -Name "GMAIL_ADDRESS"
        $Password = Get-RequiredEnv -Name "GMAIL_APP_PASSWORD"
        $Recipient = [Environment]::GetEnvironmentVariable("RECIPIENT_EMAIL", "Process")
        if ([string]::IsNullOrWhiteSpace($Recipient)) {
            $Recipient = $Sender
        }

        $Subject = "[아침 뉴스 오류] $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
        $Body = @"
아침 뉴스 자동화 실행 중 오류가 발생했습니다.

$ErrorMessage

로그 파일:
$LogPath
"@

        Send-NewsMail -Sender $Sender -Password $Password -Recipient $Recipient -Subject $Subject -HtmlBody "<pre>$([System.Net.WebUtility]::HtmlEncode($Body))</pre>"
    }
    catch {
        Write-AppLog "Failed to send error alert: $($_.Exception.Message)" "ERROR"
    }
}

trap {
    Write-AppLog "Unhandled error: $($_.Exception.Message)" "ERROR"
    Send-ErrorAlert -ErrorMessage $_.Exception.Message
    throw
}

Import-DotEnv -Path $EnvPath
Import-JsonSettings -Path $SettingsPath
Write-AppLog "Run started"

if ($ValidateOnly) {
    Write-Host "검증 완료: 스크립트가 정상적으로 로드되었습니다."
    exit 0
}

$LimitText = [Environment]::GetEnvironmentVariable("NEWS_LIMIT", "Process")
if ([string]::IsNullOrWhiteSpace($LimitText)) {
    $Limit = 5
}
else {
    $Limit = [int]$LimitText
}

$FeedUrl = Get-NewsFeedUrl
$Items = Get-MorningNews -FeedUrl $FeedUrl -Limit $Limit
if ($Items.Count -eq 0) {
    Write-Host "No news found. Skipped."
    Write-AppLog "Run finished with no news"
    exit 0
}
$BlogDraft = New-BlogDraft -Items $Items -ProjectDir $ProjectDir
$Subject = "[아침 뉴스] 주요뉴스 $($Items.Count)개 - $(Get-Date -Format 'yyyy-MM-dd')"
$HtmlBody = New-NewsHtml -Items $Items -BlogDraft $BlogDraft

if ($DryRun) {
    Write-Host $Subject
    $Items | ForEach-Object { Write-Host "- $($_.Title)" }
    if ($BlogDraft) {
        Write-Host "Blog draft: $($BlogDraft.Path)"
    }
    exit 0
}

$Sender = Get-RequiredEnv -Name "GMAIL_ADDRESS"
$Password = Get-RequiredEnv -Name "GMAIL_APP_PASSWORD"
$Recipient = [Environment]::GetEnvironmentVariable("RECIPIENT_EMAIL", "Process")
if ([string]::IsNullOrWhiteSpace($Recipient)) {
    $Recipient = $Sender
}

Send-NewsMail -Sender $Sender -Password $Password -Recipient $Recipient -Subject $Subject -HtmlBody $HtmlBody
Write-Host "$Recipient 주소로 주요뉴스 $($Items.Count)개를 보냈습니다."
if ($BlogDraft) {
    Write-Host "블로그 초안 파일: $($BlogDraft.Path)"
}
Write-AppLog "Run finished successfully. Sent $($Items.Count) news items to $Recipient"





