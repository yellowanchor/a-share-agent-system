# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    关闭后台运行的看板进程。
#>
$Root = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $Root 'logs\app.pid'
$Port = 8501

$killed = @()

if (Test-Path $PidFile) {
    $appPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($appPid -and ($appPid -match '^\d+$')) {
        $p = Get-Process -Id ([int]$appPid) -ErrorAction SilentlyContinue
        if ($p) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue; $killed += $p.Id }
    }
}

try {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | ForEach-Object {
        if ($_.OwningProcess -and ($killed -notcontains $_.OwningProcess)) {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            $killed += $_.OwningProcess
        }
    }
} catch { }

if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }

if ($killed.Count -gt 0) { Write-Host ("已停止看板进程：{0}" -f ($killed -join ', ')) -ForegroundColor Green }
else { Write-Host '没有发现正在运行的看板进程。' -ForegroundColor DarkGray }
