# zlwin provisioning: runs once, elevated, at the first logon of `zl` (from
# autounattend.xml FirstLogonCommands). Every step logs to C:\zl\setup.log;
# a failed step is listed in C:\zl\setup.failed and the run goes on, so one
# broken download still leaves a VM that can be reached and repaired. Ends by
# powering off: winvm.sh golden waits for that.
param([string]$Media)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
New-Item -ItemType Directory -Force C:\zl, C:\zl\bin, C:\zl\run, C:\zl\dl | Out-Null
Start-Transcript -Path C:\zl\setup.log -Append | Out-Null

function Step([string]$name, [scriptblock]$body) {
    Write-Output "=== $name"
    try { & $body; Write-Output "--- ok: $name" }
    catch {
        Write-Output "--- FAILED: $name : $_"
        Add-Content C:\zl\setup.failed $name
    }
}

function Fetch([string]$url, [string]$out) {
    for ($i = 1; $i -le 5; $i++) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $out; return }
        catch { if ($i -eq 5) { throw }; Start-Sleep -Seconds (10 * $i) }
    }
}

function Wait-Network {
    for ($i = 0; $i -lt 60; $i++) {
        if (Test-Connection -Quiet -Count 1 -ComputerName 1.1.1.1) { return }
        Start-Sleep -Seconds 5
    }
    throw 'no network after 5 minutes'
}

$password = '@PASSWORD@'
$pubkey = '@PUBKEY@'

Step 'copy setup media' {
    New-Item -ItemType Directory -Force C:\zl\setup | Out-Null
    Copy-Item -Force "$Media\setup\*" C:\zl\setup
}

Step 'virtio guest tools' {
    $gt = Get-PSDrive -PSProvider FileSystem | ForEach-Object { "$($_.Root)virtio-win-guest-tools.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $gt) { throw 'virtio-win-guest-tools.exe not found on any drive' }
    Start-Process -Wait -FilePath $gt -ArgumentList '/install', '/quiet', '/norestart'
}

Step 'network' {
    Wait-Network
    Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private
}

Step 'power, updates, defender, smartscreen, telemetry' {
    powercfg /change standby-timeout-ac 0
    powercfg /change monitor-timeout-ac 0
    powercfg /hibernate off
    $au = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
    New-Item -Force $au | Out-Null
    Set-ItemProperty $au NoAutoUpdate 0
    Set-ItemProperty $au AUOptions 2
    Set-ItemProperty $au NoAutoRebootWithLoggedOnUsers 1
    Add-MpPreference -ExclusionPath C:\zl, C:\msys64
    $sys = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System'
    New-Item -Force $sys | Out-Null
    Set-ItemProperty $sys EnableSmartScreen 0
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' VerifiedAndReputablePolicyState 0
    $dc = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection'
    New-Item -Force $dc | Out-Null
    Set-ItemProperty $dc AllowTelemetry 1
    # the domain's clock is UTC (win11.xml.in), whatever ZLWIN_TIMEZONE says
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation' RealTimeIsUniversal 1 -Type DWord
}

Step 'remove bundled apps' {
    $drop = 'Clipchamp|Bing|Xbox|Gaming|Solitaire|People|Teams|Todos|OutlookForWindows|Copilot|' +
        'YourPhone|GetHelp|Getstarted|Feedback|Zune|MixedReality|OfficeHub|Family|PowerAutomate|' +
        'DevHome|549981C3F5F10|WindowsMaps|SoundRecorder|Alarms|Camera'
    Get-AppxProvisionedPackage -Online | Where-Object DisplayName -match $drop |
        ForEach-Object { Remove-AppxProvisionedPackage -Online -PackageName $_.PackageName | Out-Null }
    Get-AppxPackage -AllUsers | Where-Object Name -match $drop |
        ForEach-Object { try { Remove-AppxPackage -AllUsers -Package $_.PackageFullName } catch {} }
}

Step 'remote desktop + autologon' {
    Set-ItemProperty 'HKLM:\System\CurrentControlSet\Control\Terminal Server' fDenyTSConnections 0
    Enable-NetFirewallRule -DisplayGroup 'Remote Desktop'
    $wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $wl AutoAdminLogon '1'
    Set-ItemProperty $wl DefaultUserName 'zl'
    Set-ItemProperty $wl DefaultPassword $password
    Remove-ItemProperty $wl AutoLogonCount -ErrorAction SilentlyContinue
}

Step 'msys2' {
    Fetch 'https://repo.msys2.org/distrib/msys2-x86_64-latest.sfx.exe' C:\zl\dl\msys2.sfx.exe
    Start-Process -Wait -FilePath C:\zl\dl\msys2.sfx.exe -ArgumentList '-y', '-oC:\'
    $bash = 'C:\msys64\usr\bin\bash.exe'
    & $bash -lc 'true'
    & $bash -lc 'pacman -Syuu --noconfirm'
    & $bash -lc 'pacman -Syuu --noconfirm'
    $pkgs = (Get-Content C:\zl\setup\msys2-packages.txt | Where-Object { $_ }) -join ' '
    & $bash -lc "pacman -S --noconfirm --needed $pkgs"
    foreach ($t in 'gcc', 'clang', 'python3') {
        if (-not (Test-Path "C:\msys64\ucrt64\bin\$t.exe")) { throw "ucrt64 $t missing after pacman" }
    }
}

Step 'visual studio build tools' {
    Fetch 'https://aka.ms/vs/stable/vs_BuildTools.exe' C:\zl\dl\vs_BuildTools.exe
    $p = Start-Process -Wait -PassThru -FilePath C:\zl\dl\vs_BuildTools.exe -ArgumentList `
        '--quiet', '--wait', '--norestart', '--nocache',
        '--add', 'Microsoft.VisualStudio.Workload.VCTools', '--includeRecommended'
    # 3010: success, reboot required
    if ($p.ExitCode -ne 0 -and $p.ExitCode -ne 3010) { throw "vs_BuildTools exit $($p.ExitCode)" }
}

Step 'msvc environment for bash' {
    & C:\zl\setup\msvcenv.ps1
}

Step 'openssh server' {
    $cap = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' | Select-Object -First 1
    if ($cap.State -ne 'Installed') { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
    Start-Service sshd
    Stop-Service sshd
    $cfg = 'C:\ProgramData\ssh\sshd_config'
    (Get-Content $cfg) -replace '^#?PasswordAuthentication .*', 'PasswordAuthentication no' |
        Set-Content -Encoding ascii $cfg
    $ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
    Set-Content -Encoding ascii -Path $ak -Value $pubkey
    icacls $ak /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
    New-Item -Force 'HKLM:\SOFTWARE\OpenSSH' | Out-Null
    Set-ItemProperty 'HKLM:\SOFTWARE\OpenSSH' DefaultShell 'C:\msys64\usr\bin\bash.exe'
    [Environment]::SetEnvironmentVariable('MSYSTEM', 'UCRT64', 'Machine')
    [Environment]::SetEnvironmentVariable('CHERE_INVOKING', '1', 'Machine')
    [Environment]::SetEnvironmentVariable('BASH_ENV', '/etc/zlenv.sh', 'Machine')
    Set-Service sshd -StartupType Automatic
    if (-not (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
            -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
    }
    Start-Service sshd
}

Step 'activation' {
    cscript //nologo C:\Windows\System32\slmgr.vbs /ato
    cscript //nologo C:\Windows\System32\slmgr.vbs /dli
    cscript //nologo C:\Windows\System32\slmgr.vbs /xpr
}

Write-Output '=== provisioning finished'
Set-Content C:\zl\setup.done (Get-Date -Format o)
Stop-Transcript | Out-Null
shutdown /s /t 30 /c 'zlwin provisioning finished'
