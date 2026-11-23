# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    一键更新数据 + 生成每日推荐简报。
.DESCRIPTION
    1) 快速模式：行情增量 + 新闻增量（全市场约 20~35 分钟，视网络而定）
    2) 只跑推荐：读库里的最新数据算推荐（约 1~3 分钟）
    3) 完整模式：更新 + 推荐 + Markdown 简报
    注意：DuckDB 是单写者，更新前请先关闭看板，否则会被"文件被占用"挡住。
#>
param(
    [ValidateSet('fast', 'signals', 'full', 'menu')]
    [string]$Mode = 'menu'
)

$ErrorActionPreference = 'Continue'
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
$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Log = Join-Path $LogDir "launch_daily_$Stamp.log"

function Test-PortBusy([int]$p) {
    try { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop | Out-Null; return $true }
    catch { return $false }
}

function Write-Step($msg) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor DarkCyan
    Write-Host $msg -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor DarkCyan
}

function Invoke-Py([string[]]$argList, [string]$title) {
    Write-Step $title
    $out = & $Py @argList 2>&1
    $code = $LASTEXITCODE
    $out | Tee-Object -FilePath $Log -Append | ForEach-Object { Write-Host $_ }
    if ($code -ne 0) {
        Write-Host "步骤失败（exit $code）：$title" -ForegroundColor Red
        return $false
    }
    return $true
}

Push-Location $Root
$env:PYTHONPATH = $Root
$env:PYTHONIOENCODING = 'utf-8'

if (-not (Test-Path $Py)) {
    Write-Host "找不到 Python：$Py" -ForegroundColor Red
    Pop-Location; Read-Host "按回车关闭"; exit 1
}

if (Test-PortBusy 8501) {
    Write-Host "检测到看板正在运行（端口 8501）。" -ForegroundColor Yellow
    Write-Host "DuckDB 单写者：看板只读连接会挡住写库，请先关闭看板。" -ForegroundColor Yellow
    Write-Host "关闭方法：运行 scripts\stop_app.ps1" -ForegroundColor Yellow
    $ans = Read-Host "仍要继续吗？(y/N)"
    if ($ans -ne 'y' -and $ans -ne 'Y') { Pop-Location; exit 0 }
}

if ($Mode -eq 'menu') {
    Write-Host "请选择要执行的任务：" -ForegroundColor Green
    Write-Host "  1) 只跑推荐（快，1~3 分钟）"
    Write-Host "  2) 更新数据（行情+新闻，20~35 分钟）"
    Write-Host "  3) 完整：更新 + 推荐 + 简报（30~40 分钟）"
    $pick = Read-Host "输入 1 / 2 / 3（默认 1）"
    switch ($pick) {
        '2' { $Mode = 'fast' }
        '3' { $Mode = 'full' }
        default { $Mode = 'signals' }
    }
}

$ok = $true

if ($Mode -eq 'fast' -or $Mode -eq 'full') {
    $ok = Invoke-Py @('scripts\update_daily.py', '--all', '--threads', '4', '--pace', '0.25') `
        '① 增量更新行情 + 新闻（全市场）'
    if (-not $ok) {
        Write-Host "行情/新闻更新中断，常见原因：数据源限流。" -ForegroundColor Yellow
        Write-Host "换备源重试：python scripts\update_daily.py --market --source sina --threads 4 --pace 0.2" -ForegroundColor Yellow
    }
}

if ($ok -and ($Mode -eq 'signals' -or $Mode -eq 'full')) {
    $ok = Invoke-Py @('scripts\run_daily_signals.py', '--limit', '20') '② 生成每日推荐（含现场新闻 + 闸门）'
    if ($ok) {
        $latest = Get-ChildItem (Join-Path $Root 'data\signals') -Filter '*.json' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            Write-Host ""
            Write-Host "推荐已写入：$($latest.FullName)" -ForegroundColor Green
            try {
                $j = Get-Content $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($it in $j.items | Select-Object -First 8) {
                    $v = switch ($it.verdict) { 'buy' { '建议买入' } 'watch' { '观察' } 'reject' { '否决' } default { $it.verdict } }
                    Write-Host ("  {0} {1,-6} {2,5:N1} 分  {3}  买 {4} / 止 {5} / 盈 {6}" -f `
                        $it.code, $it.name, $it.score, $v, $it.entry, $it.stop_loss, $it.take_profit)
                }
            } catch { }
        }
    }
}

if ($ok -and $Mode -eq 'full') {
    $ok = Invoke-Py @('scripts\daily_brief.py', '--top', '5', '--md', "data\signals\brief_$(Get-Date -Format 'yyyy-MM-dd').md") `
        '③ 生成 Markdown 简报'
}

Write-Host ""
if ($ok) { Write-Host "全部完成。日志：$Log" -ForegroundColor Green }
else     { Write-Host "有步骤失败，详见日志：$Log" -ForegroundColor Red }

Pop-Location
if ($Host.Name -eq 'ConsoleHost') { Read-Host "按回车关闭窗口" }
