# Golden-image maintenance, driven by `winvm.sh update-golden` over ssh.
#   -Phase windows  install every available Windows update; exit 10 when a
#                   reboot is needed before the next pass, 0 when none is left
#   -Phase tools    upgrade the Build Tools and MSYS2, install what
#                   msys2-packages.txt lists and the image lacks, then
#                   regenerate zlenv.sh
param([ValidateSet('windows', 'tools')][string]$Phase = 'windows', [switch]$AsSystem)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# The Windows Update API refuses to download for a network logon (ssh:
# E_ACCESSDENIED), so the windows phase runs itself again as SYSTEM in a
# scheduled task, waits for it, and passes on its output and exit code.
if ($Phase -eq 'windows' -and -not $AsSystem) {
    $log = 'C:\zl\update-windows.log'
    Remove-Item $log -ErrorAction SilentlyContinue
    $cmd = "& C:\zl\setup\update.ps1 -Phase windows -AsSystem *> $log; exit `$LASTEXITCODE"
    $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`""
    Register-ScheduledTask -TaskName zlupdate -Action $act -User SYSTEM -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName zlupdate
    # 267009: running; 267011: not yet run
    do {
        Start-Sleep 10
        $state = (Get-ScheduledTask -TaskName zlupdate).State
        $rc = (Get-ScheduledTaskInfo -TaskName zlupdate).LastTaskResult
    } while ($state -eq 'Running' -or $rc -eq 267009 -or $rc -eq 267011)
    Unregister-ScheduledTask -TaskName zlupdate -Confirm:$false
    if (Test-Path $log) { Get-Content $log }
    exit $rc
}

if ($Phase -eq 'windows') {
    $session = New-Object -ComObject Microsoft.Update.Session
    $found = $session.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0 and Type='Software'").Updates
    if ($found.Count -eq 0) { Write-Output 'windows update: nothing to install'; exit 0 }
    $todo = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $found) {
        if (-not $u.EulaAccepted) { $u.AcceptEula() }
        Write-Output "windows update: $($u.Title)"
        [void]$todo.Add($u)
    }
    $dl = $session.CreateUpdateDownloader(); $dl.Updates = $todo; [void]$dl.Download()
    $inst = $session.CreateUpdateInstaller(); $inst.Updates = $todo
    $r = $inst.Install()
    Write-Output "windows update: result $($r.ResultCode), reboot required $($r.RebootRequired)"
    if ($r.RebootRequired) { exit 10 }
    exit 0
}

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -property installationPath
$installer = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\setup.exe"
$p = Start-Process -Wait -PassThru -FilePath $installer -ArgumentList 'update', '--installPath', "`"$vs`"", '--quiet', '--norestart'
Write-Output "build tools update: exit $($p.ExitCode)"
& C:\msys64\usr\bin\bash.exe -lc 'pacman -Syuu --noconfirm; pacman -Syuu --noconfirm'
$pkgs = (Get-Content C:\zl\setup\msys2-packages.txt | Where-Object { $_ }) -join ' '
& C:\msys64\usr\bin\bash.exe -lc "pacman -S --noconfirm --needed $pkgs"
& C:\zl\setup\msvcenv.ps1
