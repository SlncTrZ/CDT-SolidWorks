[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$EvidenceRoot,
    [switch]$NativeOnly
)

$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$repo=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath=(Resolve-Path -LiteralPath $Python).Path
$evidence=[IO.Path]::GetFullPath($EvidenceRoot)
$helper=Join-Path $PSScriptRoot 'native_windows_gate.py'
$task='CDT-SolidWorks-Native-Gate'
$registered=$false
$code=1
$owner=[Security.Principal.WindowsIdentity]::GetCurrent().Name
if(Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue){throw 'Native gate task already exists; inspect it before running again'}
if(Test-Path -LiteralPath $evidence){throw 'Evidence directory already exists; use a new directory to preserve previous receipts'}
if(@(Get-Process SLDWORKS -ErrorAction SilentlyContinue).Count -ne 0){throw 'Pre-existing SolidWorks process; native gate refuses to change user-owned state'}
New-Item -ItemType Directory -Path $evidence | Out-Null
$launch=[ordered]@{startedUtc=(Get-Date).ToUniversalTime().ToString('o');nativeProcessesBefore=0;task=$task;taskRemoved=$false;nativeProcessesAfter=$null;exitCode=$null}
try {
    $arguments='-B "'+$helper+'" --evidence "'+$evidence+'"'
    if($NativeOnly){$arguments+=' --native-only'}
    $action=New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $repo
    $principal=New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $task -Action $action -Principal $principal -Settings $settings | Out-Null
    $registered=$true
    Start-ScheduledTask -TaskName $task
    $deadline=(Get-Date).AddMinutes(9)
    do {
        Start-Sleep -Seconds 2
        $state=[string](Get-ScheduledTask -TaskName $task).State
    } while($state -eq 'Running' -and (Get-Date) -lt $deadline)
    if($state -eq 'Running'){throw 'Native gate still running; preserve task/state for review, do not retry'}
    $info=Get-ScheduledTaskInfo -TaskName $task
    $reportPath=Join-Path $evidence 'runner-report.json'
    if(-not(Test-Path -LiteralPath $reportPath)){throw 'Native gate report missing'}
    $report=Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    if($info.LastTaskResult -ne 0 -or $report.state -ne 'passed' -or $report.pytest_exit_code -ne 0){throw 'Native gate failed; inspect receipts before any rerun'}
    $until=(Get-Date).AddSeconds(20)
    do {
        $remaining=@(Get-Process SLDWORKS -ErrorAction SilentlyContinue).Count
        if($remaining -eq 0){break}
        Start-Sleep -Seconds 1
    } while((Get-Date) -lt $until)
    if($remaining -ne 0){throw 'SolidWorks cleanup not verified; do not kill or retry'}
    [ordered]@{task=$task;taskResult=$info.LastTaskResult;sessionId=$report.windows_session_id;nativeProcessesAfter=$remaining;report=$reportPath} | ConvertTo-Json -Compress
    $code=0
} catch {
    $launch.error=$_.Exception.Message
    Write-Output ('NATIVE_GATE_ERROR='+$_.Exception.Message)
} finally {
    if($registered){
        $current=Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
        if($current -and [string]$current.State -ne 'Running'){
            Unregister-ScheduledTask -TaskName $task -Confirm:$false
            $launch.taskRemoved=$true
            Write-Output 'NATIVE_GATE_TASK_REMOVED=True'
        }
    }
    $launch.nativeProcessesAfter=@(Get-Process SLDWORKS -ErrorAction SilentlyContinue).Count
    $launch.finishedUtc=(Get-Date).ToUniversalTime().ToString('o')
    $launch.exitCode=$code
    $launch | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidence 'launch-receipt.json') -Encoding UTF8
}
exit $code
