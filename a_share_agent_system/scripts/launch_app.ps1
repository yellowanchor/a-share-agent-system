# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    一键启动 A 股舆情看板（Streamlit）。
.DESCRIPTION
    - 已在运行则只打开浏览器，不重复启动
    - 首次启动等待端口就绪后再打开浏览器，避免"页面打不开"
    - 后台静默运行，PID 写入 logs\app.pid，日志写入 logs\app_launch.log
#>
$ErrorActionPreference = 'Stop'
function Resolve-Python {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try {
        $cand = @($env:ASHARE_PYTHON)
        $cand += @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:USERPROFILE 'anaconda3\python.exe'),
            (Join-Path $env:USERPROFILE 'miniconda3\python.exe'),
            'C:\ProgramData\anaconda3\python.exe',
            'python'
        )
        foreach ($c in $cand) {
            if (-not $c) { continue }
            if ($c -ne 'python' -and -not (Test-Path $c)) { continue }
            $null = & $c -c "import streamlit" 2>&1
            if ($LASTEXITCODE -eq 0) { return $c }
        }
        return $null
    } finally {
        $ErrorActionPreference = $prev
    }
}
$Py = Resolve-Python
if (-not $Py) {
    throw '找不到装有 streamlit 的 Python：请设置环境变量 ASHARE_PYTHON 指向 python.exe 后重试'
}
$Root = Split-Path -Parent $PSScriptRoot        # a_share_agent_system
$Port = 8501
$Url  = "http://localhost:$Port"
$LogDir = Join-Path $Root 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$AppLog = Join-Path $LogDir 'app_launch.log'
$PidFile = Join-Path $LogDir 'app.pid'

function Test-PortBusy([int]$p) {
    try { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

if (-not (Test-Path $Py)) {
    Write-Host "找不到 Python：$Py" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}

if (Test-PortBusy $Port) {
    Write-Host "看板已在运行，直接打开浏览器 → $Url" -ForegroundColor Green
    Start-Process $Url
    Start-Sleep -Seconds 1
    exit 0
}

Write-Host "正在启动看板（首次约 10~20 秒，请稍候）…" -ForegroundColor Cyan

$env:PYTHONPATH = $Root
$env:PYTHONIOENCODING = 'utf-8'

$argList = @(
    '-m', 'streamlit', 'run', 'src\app\main.py',
    '--server.port', "$Port",
    '--server.address', 'localhost',
    '--server.headless', 'true',
    '--server.fileWatcherType', 'none',
    '--browser.gatherUsageStats', 'false'
)

$proc = Start-Process -FilePath $Py -ArgumentList $argList -WorkingDirectory $Root `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $AppLog -RedirectStandardError "$AppLog.err"
$proc.Id | Set-Content -Path $PidFile -Encoding utf8

$ready = $false
for ($i = 0; $i -lt 80; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-PortBusy $Port) { $ready = $true; break }
}

if ($ready) {
    Start-Process $Url
    Write-Host "已打开：$Url" -ForegroundColor Green
    Write-Host "（关闭：运行 scripts\stop_app.ps1，或结束 PID $(Get-Content $PidFile)）" -ForegroundColor DarkGray
} else {
    Write-Host "启动超时，请查看日志：$AppLog" -ForegroundColor Red
    Read-Host "按回车关闭"
    exit 1
}
