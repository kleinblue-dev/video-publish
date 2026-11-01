<#
  video-publish installer (Windows PowerShell 5.1+)
  1) detect ffprobe / biliup / cookies / douyin playwright publisher on this machine
  2) write %USERPROFILE%\.video-publish\config.json (kept if exists unless -Force)
  3) copy the skill into %USERPROFILE%\.dsh\skills\video-publish (new session picks it up)

  Usage:
    powershell -ExecutionPolicy Bypass -File .\install.ps1
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -Force       # rewrite config
    powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoInstall   # config only

  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
  UTF-8 scripts as ANSI and would break on non-ASCII text.
#>
param(
    [string]$SkillHome = (Join-Path $env:USERPROFILE ".dsh\skills"),
    [string]$ConfigPath = (Join-Path $env:USERPROFILE ".video-publish\config.json"),
    [switch]$Force,
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Find-Exe([string]$name, [string[]]$extra) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in $extra) {
        if ($candidate -and (Test-Path $candidate)) { return (Resolve-Path $candidate).Path }
    }
    return ""
}

$ffprobe = Find-Exe "ffprobe" @("D:\2006L\tools\ffmpeg\bin\ffprobe.exe", "C:\ffmpeg\bin\ffprobe.exe")
$ffmpeg = Find-Exe "ffmpeg" @("D:\2006L\tools\ffmpeg\bin\ffmpeg.exe", "C:\ffmpeg\bin\ffmpeg.exe")
if (-not $ffmpeg -and $ffprobe) {
    $guess = Join-Path (Split-Path -Parent $ffprobe) "ffmpeg.exe"
    if (Test-Path $guess) { $ffmpeg = $guess }
}

$biliup = Find-Exe "biliup" @()
if (-not $biliup) {
    foreach ($base in @("D:\2006L\biliup", (Join-Path $env:USERPROFILE "biliup"))) {
        if (Test-Path $base) {
            $hit = Get-ChildItem $base -Recurse -Filter "biliup.exe" -ErrorAction SilentlyContinue |
                Sort-Object FullName -Descending | Select-Object -First 1
            if ($hit) { $biliup = $hit.FullName; break }
        }
    }
}

$cookies = ""
if ($biliup) {
    $candidate = Join-Path (Split-Path -Parent $biliup) "cookies.json"
    if (Test-Path $candidate) { $cookies = $candidate }
}
if (-not $cookies) {
    foreach ($base in @("D:\2006L\biliup", (Join-Path $env:USERPROFILE "biliup"))) {
        $candidate = Join-Path $base "cookies.json"
        if (Test-Path $candidate) { $cookies = $candidate; break }
    }
}

$python = ""
foreach ($candidate in @("E:\2006L\guichao\.venv\Scripts\python.exe", "python")) {
    $found = Find-Exe $candidate @()
    if ($found) { $python = $found; break }
}

$douyinRoot = "D:\2006L\tools\douyin-auto"
$douyinPublisher = Join-Path $douyinRoot "publish_daily.py"
$douyinProfile = Join-Path $douyinRoot "profile"

$config = [ordered]@{
    schema = 1
    tools  = [ordered]@{
        ffprobe          = $ffprobe
        ffmpeg           = $ffmpeg
        biliup           = $biliup
        python           = $python
        douyin_publisher = $(if (Test-Path $douyinPublisher) { $douyinPublisher } else { "" })
        douyin_python    = $python
    }
    bilibili = [ordered]@{
        cookies_path   = $cookies
        proxy          = ""
        schedule_times = @("12:00", "19:00")
        tid            = 231
        submit         = "client"
        timeout_sec    = 900
    }
    douyin = [ordered]@{
        profile_dir    = $(if (Test-Path $douyinProfile) { $douyinProfile } else { "" })
        shot_root      = (Join-Path $douyinRoot "shots")
        result_root    = $douyinRoot
        schedule_times = @("12:00", "19:00")
        timeout_sec    = 900
    }
    ledger = (Join-Path $env:USERPROFILE ".video-publish\ledger.jsonl")
    rules  = ""
}

Write-Host "Detected:"
Write-Host ("  ffprobe    : " + $(if ($ffprobe)  { $ffprobe }  else { "(missing)" }))
Write-Host ("  biliup     : " + $(if ($biliup)   { $biliup }   else { "(missing)" }))
Write-Host ("  cookies    : " + $(if ($cookies)  { $cookies }  else { "(missing)" }))
Write-Host ("  python     : " + $(if ($python)   { $python }   else { "(missing)" }))
Write-Host ("  douyin pub : " + $(if (Test-Path $douyinPublisher) { $douyinPublisher } else { "(missing)" }))
Write-Host ("  douyin prof: " + $(if (Test-Path $douyinProfile)   { $douyinProfile }   else { "(missing)" }))

if ((Test-Path $ConfigPath) -and -not $Force) {
    Write-Host ""
    Write-Host "Config exists, kept (use -Force to rewrite): $ConfigPath"
} else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ConfigPath) | Out-Null
    ($config | ConvertTo-Json -Depth 6) | Set-Content -Path $ConfigPath -Encoding UTF8
    Write-Host ""
    Write-Host "Config written: $ConfigPath"
}

if ($NoInstall) { exit 0 }

$target = Join-Path $SkillHome "video-publish"
if (Test-Path $target) {
    Write-Host ""
    Write-Host "Updating existing skill dir: $target"
    Remove-Item $target -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
$skip = @(".git", "__pycache__", ".pytest_cache")
Get-ChildItem $root -Force | Where-Object { $skip -notcontains $_.Name -and $_.Extension -ne ".pyc" } | ForEach-Object {
    Copy-Item $_.FullName -Destination $target -Recurse -Force
}
Get-ChildItem $target -Recurse -Force -Include "*.pyc" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
Write-Host "Installed to: $target"
Write-Host ""
Write-Host "Next:"
Write-Host ("  python `"" + (Join-Path $target "publish.py") + "`" doctor")
Write-Host ("  python `"" + (Join-Path $target "publish.py") + "`" check --job job.json")
Write-Host "The skill catalog is scanned when a new session starts; this session will not see it."
