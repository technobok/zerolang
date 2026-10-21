# Windows testing

zerolang is developed on Linux and tested on Windows in three ways, from
cheapest to most real:

| Make target | What it runs | Needs |
|---|---|---|
| `make test-mingw` | the whole run corpus, built for Windows by mingw-w64, run under wine | mingw-w64 + wine |
| `make test-tcc-mingw` | the whole run corpus, built for Windows by the vendored tcc (cross-built for Windows, linking the UCRT), run under wine | wine |
| `make test-msvc-wine` | MSVC itself (`cl.exe` under wine) builds and runs the thread shim's test | wine + msvc-wine (below) |
| `make ci-windows` | on a real Windows machine: the shim under MSVC and UCRT64 gcc; every corpus binary; non-ASCII text on a console; a file given by its drive path to zc and zl; zc, zl and zls built natively there passing `make test`; the vendored tcc built natively there passing `make test-tcc`, `make mode-parity` (in-process through `libtcc.dll`) and `make cobj-guard` | a Windows host over ssh, e.g. the VM below |

The first three are part of `make ci` and print `skipped (needs ...)` without
their tools. `make ci-windows` is not, since it needs the machine. Run it at
stage gates and commit the `tests/windows-verified.txt` a green run writes;
`make ci` ends with `make windows-due`, which says when a run is due again.
`tests/mingw-known-failures.txt` lists only the run cases marked `posix` in
`tests/fixtures/run_cases.txt` (POSIX file descriptors and signals, which
Windows does not have); `tests/tcc-mingw-known-failures.txt` adds what tcc
cannot build on any system (`f128`), each with its reason. Wine is not
Windows: it maps POSIX paths such as `/tmp` and accepts paths past `MAX_PATH`
without the `\\?\` prefix, so only `make ci-windows` proves a case portable.

## 1. mingw-w64 under wine (`make test-mingw`)

```bash
sudo apt install gcc-mingw-w64-x86-64 wine
make test-mingw
```

The wine prefix lives in `out/wineprefix` and is created on first use.

## 2. MSVC under wine (`make test-msvc-wine`)

[msvc-wine](https://github.com/mstorsjo/msvc-wine) downloads MSVC and the
Windows SDK from Microsoft and runs `cl`/`link` under wine. Microsoft's Visual
Studio Build Tools licence applies; `--accept-license` says you accept it. It
needs about 6 GB.

```bash
sudo apt install wine msitools winbind python3 git
tools/windows/msvc-wine.sh --accept-license ~/msvc-wine
make test-msvc-wine MSVC_WINE=~/msvc-wine/msvc/bin/x64
```

Pass the same `MSVC_WINE=` to `make ci`, or that leg is skipped. Do not put
that `bin/x64` on your `PATH`: its `link`, `lib` and `cmd` would shadow the
host's own tools.

## 3. A Windows VM for `make ci-windows`

`tools/windows/winvm.sh` builds and drives a Windows 11 Pro VM ("zlwin") under
KVM/libvirt. The install is unattended from Microsoft's own ISO. It adds
OpenSSH, MSYS2 (make, git, UCRT64 gcc) and the Visual Studio Build Tools
(MSVC), keeps the result as a golden image, and starts every run from a fresh
copy of it.

### Host requirements

- Linux x86-64 with hardware virtualisation (AMD-V / VT-x) enabled, so
  `/dev/kvm` exists.
- About 8 GB of RAM and 8 CPUs free while the VM runs (`ZLWIN_MEMORY_GIB`,
  `ZLWIN_VCPUS`).
- Disk: about 25 GB for the golden image, about 10 GB of ISOs, and a few GB
  per run.
- The packages:

  ```bash
  sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
      virtinst ovmf swtpm swtpm-tools xorriso python3 python3-pil curl openssh-client \
      virt-viewer
  sudo usermod -aG libvirt,kvm "$USER"    # then log out and in again
  ```

  `python3-pil` is only for `winvm.sh screenshot`, and `virt-viewer` only for
  watching the VM.

### One-time setup

1. **The image directory** (it holds the disks and ISOs; libvirt's own
   directory is root-owned):

   ```bash
   sudo install -d -o "$USER" -g "$USER" -m 0755 /var/lib/libvirt/images/zlwin
   ```

2. **The licence (optional).** On a PC that shipped with Windows, the firmware
   holds an OEM key. Copy its table and the VM installs the matching edition
   from it:

   ```bash
   sudo install -m 0640 -o "$USER" -g kvm /sys/firmware/acpi/tables/MSDM \
       /var/lib/libvirt/images/zlwin/msdm.bin
   ```

   Without it, Windows installs unactivated: a watermark and locked
   personalisation, but builds, ssh and Remote Desktop are unaffected.
   Microsoft may refuse to activate an OEM key inside a VM (`0xC004C008`);
   the VM then runs unactivated just the same.

3. **The Windows 11 ISO.** Download the "Windows 11 Disk Image (ISO) for
   x64 devices", multi-edition, from
   <https://www.microsoft.com/software-download/windows11>, then:

   ```bash
   sudo install -m 0644 -o "$USER" ~/Downloads/Win11_*.iso /var/lib/libvirt/images/zlwin/win11.iso
   ```

   If the site refuses with message code 715-123130, it is objecting to the
   browser or network. Turn off tracking protection and ad blockers for the
   page, or use another browser (Edge or Chromium), or another network.

4. **Build the golden image** (about 30 minutes, no input needed):

   ```bash
   tools/windows/winvm.sh golden
   ```

   It checks the host first and names anything missing. It fetches the virtio
   drivers, generates the VM's password and ssh key (in
   `~/.local/state/zerolang-winvm`), installs, provisions, and powers off.
   Then check that every provisioning step succeeded:

   ```bash
   tools/windows/winvm.sh up
   tools/windows/winvm.sh ssh cat /c/zl/setup.failed    # absent: no step failed
   tools/windows/winvm.sh ssh 'cl 2>&1 | head -1; gcc --version | head -1'
   tools/windows/winvm.sh down
   ```

### Running

```bash
tools/windows/winvm.sh ci        # up, make ci-windows, down (about 12 minutes)
git add tests/windows-verified.txt && git commit -m "windows: record a green ci-windows"
```

Other commands: `up`, `down`, `status`, `ssh [cmd]`, `screenshot out.png`,
`update-golden`, `golden --force` (rebuild from scratch). Run `winvm.sh` with
no arguments for the list. Do not run the VM during a benchmark (`make
perf-strict`). Set `ZLWIN_BUSY_FILE` to a lock file your benchmark holds, and
`up`/`golden` refuse while it exists.

### Watching the VM

- **On the host's desktop:** `virt-manager`, or `tools/windows/winvm.sh
  console`.
- **From another machine over ssh** (Linux, WSL or macOS): the display
  (SPICE) listens only on the host's localhost, so tunnel it.
  `virsh -c qemu:///system domdisplay zlwin` prints its port.

  ```bash
  ssh -N -L 5900:127.0.0.1:5900 <user>@<host> &
  remote-viewer spice://127.0.0.1:5900
  ```

- **Remote Desktop**, once installed. The VM is on libvirt's private network,
  so tunnel that too, then connect any RDP client to `localhost:13389` as user
  `zl`, with the password in `~/.local/state/zerolang-winvm/password` on the
  host:

  ```bash
  ssh -N -L 13389:192.168.122.50:3389 <user>@<host> &
  ```

  Use a local port other than 3389: on a Windows client, 3389 may belong to
  its own Remote Desktop server, and `mstsc` refuses to connect to the
  machine's own loopback on 3389. On Windows the built-in OpenSSH client can
  run the tunnel directly (PowerShell, the same command without `&`); from
  WSL it also works, through WSL's localhost forwarding.

### Maintenance

- **Monthly:** `tools/windows/winvm.sh update-golden` applies Windows Update
  and upgrades the Build Tools and MSYS2 inside the golden image, then powers
  off. Confirm with `winvm.sh ci`. Windows Update never runs by itself: it is
  set to notify only, and each run starts from the golden image anyway.
- **To start over:** `tools/windows/winvm.sh golden --force`. A rebuild
  makes a new ssh host key; winvm.sh forgets the old one itself.

### Settings

| Variable | Default | |
|---|---|---|
| `ZLWIN_IMG` | `/var/lib/libvirt/images/zlwin` | disks and ISOs |
| `ZLWIN_STATE` | `~/.local/state/zerolang-winvm` | password, ssh key, known_hosts, ssh config |
| `ZLWIN_IP`, `ZLWIN_MAC` | `192.168.122.50`, `52:54:00:5a:4c:01` | on libvirt's default network |
| `ZLWIN_VCPUS`, `ZLWIN_MEMORY_GIB`, `ZLWIN_DISK_GB` | `8`, `8`, `120` | |
| `ZLWIN_TIMEZONE` | `UTC` | a Windows zone name, e.g. `Pacific Standard Time` (the virtual hardware clock is UTC whatever the zone; a golden built before RealTimeIsUniversal was set needs `golden --force` for a zone other than UTC) |
| `ZLWIN_BUSY_FILE` | none | `up`/`golden` refuse while this file exists |

### Another Windows machine instead of the VM

`make ci-windows WINDOWS_SSH=<host> [WINDOWS_SSH_FLAGS=...]` works against any
Windows machine whose ssh login meets these conditions (this is what
`setup/firstlogon.ps1` and `setup/msvcenv.ps1` arrange in the VM):

- OpenSSH Server, with key login.
- MSYS2 bash as sshd's shell (`HKLM\SOFTWARE\OpenSSH\DefaultShell`).
- `make`, `tar`, UCRT64 `gcc` and MSVC's `cl`/`link` on PATH, ahead of
  MSYS2's coreutils `link`. A bash that sshd starts reads `~/.bashrc` under
  the Windows profile, not `BASH_ENV`, so source the vcvars environment there.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ci-windows: skipped (no ssh to zlwin)` | Use `tools/windows/winvm.sh ci`, which starts the VM and passes its ssh config. Plain `make ci-windows` needs a reachable `WINDOWS_SSH`. |
| `ci-windows FAIL: ... has no make cl ...` | The guest's ssh PATH is incomplete; see "Another Windows machine". In the VM, rebuild with `golden --force`. |
| `winvm: ... not in group libvirt` | `sudo usermod -aG libvirt,kvm $USER`, then log in again. |
| `winvm: no ssh to zl@... within 5 minutes` | Look at the screen (`winvm.sh screenshot s.png`, or a viewer). A first boot after `update-golden` can take longer. |
| `golden` sits at a firmware screen | The ISO's "press any key" prompt was missed. `winvm.sh golden --force` again. |
| `Host key verification failed` | Something other than winvm.sh reached the VM before a rebuild. Delete `~/.local/state/zerolang-winvm/known_hosts`. |
| cl: `D8021`, or arguments treated as paths | MSYS2 rewrites `/flag` arguments as paths. Spell cl's flags `-flag` (`-nologo -Fe:x.exe`). |
| `link.exe`: `LNK4044 unrecognized option '/home/...'` | link reads an absolute Unix path as an option. Keep the paths you give cl relative. |
| `test-msvc-wine` fails with no output | `winbind` missing, or a stale `out/wineprefix`: `rm -rf out/wineprefix` and retry. |
