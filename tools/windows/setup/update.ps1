# Golden-image maintenance, driven by `winvm.sh update-golden` over ssh.
#   -Phase windows  install every available Windows update; exit 10 when a
#                   reboot is needed before the next pass, 0 when none is left
#   -Phase tools    upgrade the Build Tools and MSYS2, then regenerate zlenv.sh
param([ValidateSet('windows', 'tools')][string]$Phase = 'windows')
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

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
& C:\zl\setup\msvcenv.ps1
