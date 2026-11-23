# 注册 / 卸载 A 股舆情系统的每日定时任务（Windows 计划任务）
#
# 用法（在 a_share_agent_system 目录或任意位置均可）：
#   pwsh -NoProfile -File scripts/install_tasks.ps1              # 注册 3 个任务
#   pwsh -NoProfile -File scripts/install_tasks.ps1 -List        # 只看当前状态
#   pwsh -NoProfile -File scripts/install_tasks.ps1 -Unregister  # 全部删除
#   pwsh -NoProfile -File scripts/install_tasks.ps1 -WhatIf      # 只打印将执行的命令
#
# 设计要点：
#   1) DuckDB 只允许一个写者 —— 三个任务时间错开 30 分钟以上，且 update_daily 内部
#      串行执行 行情 → 新闻 → 向量增量，绝不并发写库。
#   2) 每天 17:45 更新数据（收盘后），18:50 生成次日推荐，每月 1 日 03:00 归档冷数据。
#   3) 任务以"仅当用户登录时运行"注册，不需要保存密码。

param(
    [switch]$Unregister,
    [switch]$List,
    [switch]$WhatIf,
    [string]$Python = $env:ASHARE_PYTHON,
    [string]$LogDir = "",
    [int]$MarketThreads = 4,
    [double]$Pace = 0.3
)

if (-not $Python) {
    $cand = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:USERPROFILE 'anaconda3\python.exe'),
        (Join-Path $env:USERPROFILE 'miniconda3\python.exe'),
        'C:\ProgramData\anaconda3\python.exe'
    )
    $Python = $cand | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Python) { throw '找不到 Python：请先设置环境变量 ASHARE_PYTHON 指向 python.exe' }

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $LogDir) { $LogDir = Join-Path $Root "logs" }
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

$Tasks = @(
    @{
        Name = "ASHARE_Daily_Update"
        Desc = "A股系统-每日行情+新闻+向量索引增量更新"
        Time = "17:45"
        Exec = $Python
        Args = @("-u", "scripts/update_daily.py", "--all", "--rag",
                  "--threads", "$MarketThreads", "--pace", "$Pace")
        Log  = Join-Path $LogDir "sched_update.log"
        Limit = "02:00:00"
    },
    @{
        Name = "ASHARE_Daily_Signals"
        Desc = "A股系统-生成每日建议入手股（含新闻闸门）"
        Time = "18:50"
        Exec = $Python
        Args = @("-u", "scripts/run_daily_signals.py", "--limit", "20")
        Log  = Join-Path $LogDir "sched_signals.log"
        Limit = "01:00:00"
    },
    @{
        Name = "ASHARE_Monthly_Archive"
        Desc = "A股系统-每月归档 120 天以前的新闻到冷库"
        Time = "03:00"
        Monthly = $true
        Exec = $Python
        Args = @("-u", "scripts/archive_cold.py", "--days", "120", "--compact")
        Log  = Join-Path $LogDir "sched_archive.log"
        Limit = "01:00:00"
    }
)
function Show-Tasks {
    $existing = Get-ScheduledTask -TaskPath "\" -ErrorAction SilentlyContinue |
                Where-Object { $_.TaskName -like "ASHARE_*" }
    if (-not $existing) { Write-Host "（无已注册的 ASHARE_* 任务）"; return }
    foreach ($t in $existing) {
        $info = Get-ScheduledTaskInfo -TaskName $t.TaskName
        Write-Host ("{0,-24} {1,-10} 上次运行 {2}  结果 {3}" -f `
            $t.TaskName, $t.State, $info.LastRunTime, $info.LastTaskResult)
    }
}

if ($List) { Show-Tasks; return }

if ($Unregister) {
    foreach ($t in $Tasks) {
        if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false
            Write-Host "已删除 $($t.Name)"
        }
    }
    return
}

if (-not (Test-Path $Python)) { throw "找不到 Python：$Python（用 -Python 指定解释器路径）" }

foreach ($t in $Tasks) {
    # cmd /c 包一层，把 stdout/stderr 追加到日志文件（计划任务本身不支持重定向）
    $inner = "`"$($t.Exec)`" " + (($t.Args | ForEach-Object { "`"$_`"" }) -join " ")
    $cmdLine = "cmd.exe /c $inner >> `"$($t.Log)`" 2>&1"

    if ($WhatIf) {
        Write-Host "[WhatIf] $($t.Name) @ $($t.Time)$(if($t.Monthly){'（每月 1 日）'})"
        Write-Host "         $cmdLine"
        continue
    }

    if ($t.Monthly) {
        # 每月 1 日：PowerShell 的 ScheduledTask 触发器不支持"每月"，用 schtasks 注册
        $tr = "/c $inner >> `"$($t.Log)`" 2>&1"
        & schtasks.exe /Create /F /SC MONTHLY /D 1 /ST $t.Time `
            /TN $t.Name /TR $tr /RL LIMITED | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "注册 $($t.Name) 失败（schtasks 退出码 $LASTEXITCODE）" }
        Write-Host ("已注册 {0}  每月 1 日 {1}  日志 {2}" -f $t.Name, $t.Time, $t.Log)
        continue
    }

    $action = New-ScheduledTaskAction -Execute "cmd.exe" `
        -Argument "/c $inner >> `"$($t.Log)`" 2>&1" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At $t.Time
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Parse($t.Limit)) -MultipleInstances IgnoreNew `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings `
        -Description $t.Desc
    Register-ScheduledTask -TaskName $t.Name -InputObject $task -Force | Out-Null
    Write-Host ("已注册 {0}  每天 {1}  日志 {2}" -f $t.Name, $t.Time, $t.Log)
}

if (-not $WhatIf) {
    Write-Host ""
    Write-Host "当前任务状态："
    Show-Tasks
    Write-Host ""
    Write-Host "提示：任务在用户登录时运行；笔记本务必保持插电并关闭睡眠，否则 17:45 会漏跑。"
}

