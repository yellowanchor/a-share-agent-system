# -*- coding: utf-8 -*-
<#
.SYNOPSIS
    在桌面创建**唯一**一个入口：打开看板。
.DESCRIPTION
    只保留一个快捷方式（双击 → 静默启动看板并自动打开浏览器）。
    数据更新已经搬进网页的「数据更新」页；关闭看板由看板自己负责
    （页面关掉后后端 8 秒内自动退出），所以不再需要额外的桌面图标。
    可重复执行：会顺手删掉旧版本残留的「毕设数据更新」「关闭毕设看板」。
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
$Py = Resolve-Python   # 只有图标缺失、需要现场生成时才用得到它
$Root = Split-Path -Parent $PSScriptRoot
$Desktop = [Environment]::GetFolderPath('Desktop')
$Icon = Join-Path $Root 'docs\assets\app.ico'
$Silent = Join-Path $Root 'scripts\launch_app_silent.vbs'

if (-not (Test-Path $Icon)) {
    if (-not $Py) {
        throw '图标缺失且找不到 Python：请设置环境变量 ASHARE_PYTHON 指向 python.exe 后重试'
    }
    & $Py (Join-Path $Root 'scripts\make_icon.py') | Out-Null
}

# 清理旧入口（只保留一个图标）
foreach ($old in @('毕设数据更新.lnk', '关闭毕设看板.lnk')) {
    $p = Join-Path $Desktop $old
    if (Test-Path $p) {
        Remove-Item $p -Force
        Write-Host "已移除旧入口：$old" -ForegroundColor DarkYellow
    }
}

$shell = New-Object -ComObject WScript.Shell
$path = Join-Path $Desktop '毕设看板.lnk'
$sc = $shell.CreateShortcut($path)
$sc.TargetPath = "$env:WINDIR\System32\wscript.exe"
$sc.Arguments = "`"$Silent`""
$sc.WorkingDirectory = $Root
$sc.IconLocation = "$Icon,0"
$sc.Description = '打开 A 股舆情与量化策略看板（静默启动，自动开浏览器；关网页即自动退出）'
$sc.WindowStyle = 1
$sc.Save()

Write-Host "已创建：$path" -ForegroundColor Green
Write-Host ''
Write-Host '桌面只保留这一个入口：' -ForegroundColor Cyan
Write-Host '  · 数据更新 → 看板内「数据更新」页'
Write-Host '  · 关闭看板 → 直接关掉浏览器标签页即可'
