#!/usr/bin/env bash
# winvm.sh -- the Windows 11 VM behind `make ci-windows`: one libvirt domain
# (qemu:///system, KVM), built unattended from Microsoft's ISO and driven over
# ssh. See tools/windows/README.md for bringing it up on a new host.
#
#   init            check the host, fetch the virtio drivers, make the ssh key
#   golden [--force]  install Windows + the toolchain into golden.qcow2 (~30 min,
#                   unattended); it powers off when done
#   up / down       start a fresh run (a new overlay on golden.qcow2) / stop it
#   status          the domain's state
#   ssh [cmd]       a shell, or one command, in the VM (MSYS2 bash: cl, link,
#                   make and UCRT64 gcc on PATH)
#   ci              up, `make ci-windows`, down
#   update-golden   Windows Update + Build Tools + MSYS2 upgrades into golden
#   screenshot PNG  the VM's screen
#   console / rdp   a viewer, on a host with a display
#
# Settings (environment): ZLWIN_IMG the image directory, ZLWIN_STATE the per-host
# state (password, ssh key, known_hosts, ssh config), ZLWIN_IP / ZLWIN_MAC,
# ZLWIN_VCPUS, ZLWIN_MEMORY_GIB, ZLWIN_DISK_GB, ZLWIN_TIMEZONE (a Windows zone
# name), ZLWIN_BUSY_FILE (up and golden refuse while it exists).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ZL=$(cd "$HERE/../.." && pwd)
IMG=${ZLWIN_IMG:-/var/lib/libvirt/images/zlwin}
STATE=${ZLWIN_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/zerolang-winvm}
IP=${ZLWIN_IP:-192.168.122.50}
MAC=${ZLWIN_MAC:-52:54:00:5a:4c:01}
VCPUS=${ZLWIN_VCPUS:-8}
MEMORY_GIB=${ZLWIN_MEMORY_GIB:-8}
DISK_GB=${ZLWIN_DISK_GB:-120}
TIMEZONE=${ZLWIN_TIMEZONE:-UTC}
BUSY=${ZLWIN_BUSY_FILE:-}
DOM=zlwin
OVMF_CODE=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd
OVMF_VARS=/usr/share/OVMF/OVMF_VARS_4M.ms.fd
VIRTIO_URL=https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso
APT="sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients virtinst ovmf swtpm swtpm-tools xorriso python3 curl openssh-client"
DOC="see tools/windows/README.md"

die() { echo "winvm: $*" >&2; exit 1; }

# virsh against the system daemon; through `sg libvirt` until a fresh login
# carries the group
V() {
    if [[ " $(id -nG) " == *" libvirt "* ]]; then
        virsh -q -c qemu:///system "$@"
    else
        sg libvirt -c "virsh -q -c qemu:///system $(printf '%q ' "$@")"
    fi
}

state() { V domstate "$DOM" 2> /dev/null || echo "undefined"; }

# the host prerequisites, each failure naming its fix
check_host() {
    local c miss=""
    for c in virsh qemu-img xorriso python3 curl ssh ssh-keygen; do
        command -v "$c" > /dev/null 2>&1 || miss+=" $c"
    done
    [ -z "$miss" ] || die "missing:$miss -- install with: $APT ($DOC)"
    [ -r "$OVMF_CODE" ] && [ -r "$OVMF_VARS" ] || die "no secure-boot UEFI firmware ($OVMF_CODE) -- $APT"
    command -v swtpm > /dev/null 2>&1 || die "no swtpm (the VM's TPM 2.0) -- $APT"
    [ -e /dev/kvm ] || die "no /dev/kvm: enable virtualisation (AMD-V / VT-x) in the firmware"
    [[ " $(id -nG "$USER") " == *" libvirt "* ]] \
        || die "$USER is not in group libvirt -- sudo usermod -aG libvirt,kvm $USER, then log in again"
    V uri > /dev/null 2>&1 || die "cannot reach libvirt (qemu:///system) -- is libvirtd running? sudo systemctl enable --now libvirtd"
    [ -d "$IMG" ] && [ -w "$IMG" ] \
        || die "$IMG missing or not writable -- sudo install -d -o $USER -g $USER -m 0755 $IMG ($DOC)"
}

# define the domain with DISK as its disk and the given ISOs as SATA CD-ROMs
# (the first boots first)
define() {
    local disk=$1; shift
    local cds="" letters=(a b c d) i=0 iso acpi=""
    for iso in "$@"; do
        cds+="    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='$iso'/>
      <target dev='sd${letters[$i]}' bus='sata'/>
      <readonly/>"
        [ $i -eq 0 ] && cds+="
      <boot order='1'/>"
        cds+="
    </disk>
"
        i=$((i + 1))
    done
    # the firmware's OEM licence table, passed through when the host has one
    [ -r "$IMG/msdm.bin" ] && acpi="    <acpi>
      <table type='msdm'>$IMG/msdm.bin</table>
    </acpi>
"
    local xml="$STATE/domain.xml"
    python3 -I - "$HERE/win11.xml.in" "$xml" "$IMG" "$disk" "$cds" "$acpi" "$VCPUS" "$MEMORY_GIB" "$MAC" <<'EOF'
import sys
src, out, img, disk, cds, acpi, vcpus, mem, mac = sys.argv[1:]
t = open(src).read()
for k, v in (('@IMG@', img), ('@DISK@', disk), ('@CDROMS@', cds.rstrip('\n')), ('@ACPI@', acpi),
             ('@VCPUS@', vcpus), ('@CORES@', str(max(1, int(vcpus) // 2))), ('@MEMORY_GIB@', mem), ('@MAC@', mac)):
    t = t.replace(k, v)
import xml.dom.minidom
xml.dom.minidom.parseString(t)
open(out, 'w').write(t)
EOF
    V define "$xml" > /dev/null
}

wait_off() {
    local limit=$1 t=0
    while [ "$(state)" != "shut off" ]; do
        [ $t -ge "$limit" ] && return 1
        sleep 10; t=$((t + 10))
    done
}

ssh_zl() { ssh -F "$STATE/ssh_config" -o BatchMode=yes -o ConnectTimeout=5 zlwin "$@"; }

wait_ssh() {
    local limit=$1 t=0
    until ssh_zl true 2> /dev/null; do
        [ $t -ge "$limit" ] && return 1
        sleep 5; t=$((t + 5))
    done
}

refuse_when_busy() {
    [ -n "$BUSY" ] && [ -e "$BUSY" ] && die "$BUSY exists: something that must run alone (a benchmark) is running"
    return 0
}

cmd_init() {
    check_host
    mkdir -p "$STATE"; chmod 0700 "$STATE"
    [ -f "$STATE/id_ed25519" ] || ssh-keygen -q -t ed25519 -N '' -C zlwin -f "$STATE/id_ed25519"
    chmod 0600 "$STATE/id_ed25519"
    if [ ! -f "$STATE/password" ]; then
        (umask 077; head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 20 > "$STATE/password")
    fi
    # IgnoreUnknown: WarnWeakCrypto (the VM's OpenSSH predates post-quantum key
    # exchange) is newer than some clients
    printf 'IgnoreUnknown WarnWeakCrypto\nHost zlwin\n\tHostname %s\n\tUser zl\n\tIdentityFile %s\n\tUserKnownHostsFile %s\n\tStrictHostKeyChecking accept-new\n\tWarnWeakCrypto no-pq-kex\n' \
        "$IP" "$STATE/id_ed25519" "$STATE/known_hosts" > "$STATE/ssh_config"
    if [ ! -r "$IMG/virtio-win.iso" ]; then
        echo "init: fetching the virtio drivers ($VIRTIO_URL)"
        curl -fL --retry 3 -o "$IMG/virtio-win.iso.part" "$VIRTIO_URL" && mv "$IMG/virtio-win.iso.part" "$IMG/virtio-win.iso"
    fi
    if ! V net-info default > /dev/null 2>&1; then
        die "libvirt's default NAT network is missing -- sudo virsh net-define /usr/share/libvirt/networks/default.xml && sudo virsh net-autostart default && sudo virsh net-start default"
    fi
    # no `| grep -q` under pipefail: grep's early exit fails the writer (SIGPIPE)
    [[ "$(V net-info default)" == *"Active:"*([[:space:]])"yes"* ]] || V net-start default > /dev/null
    if [[ "$(V net-dumpxml default)" != *"$MAC"* ]]; then
        V net-update default add ip-dhcp-host "<host mac='$MAC' name='zlwin' ip='$IP'/>" --live --config
    fi
    [ -r "$IMG/msdm.bin" ] && lic="the firmware's OEM key (msdm.bin)" || lic="no key: Windows installs unactivated"
    echo "init OK: images in $IMG, state in $STATE, VM at $IP; licence: $lic"
}

cmd_mkiso() {
    local st="$STATE/iso"
    rm -rf "$st"; mkdir -p "$st/setup"
    python3 -I - "$HERE" "$st" "$STATE/password" "$STATE/id_ed25519.pub" "$TIMEZONE" <<'EOF'
import sys, os
here, st, pwf, pubf, tz = sys.argv[1:]
subs = {'@PASSWORD@': open(pwf).read().strip(), '@PUBKEY@': open(pubf).read().strip(), '@TIMEZONE@': tz}
def sub(src, dst):
    t = open(src, encoding='utf-8').read()
    for k, v in subs.items():
        t = t.replace(k, v)
    if dst.endswith('.xml'):
        import xml.dom.minidom
        xml.dom.minidom.parseString(t)
    open(dst, 'w', encoding='utf-8', newline='\r\n').write(t)
sub(os.path.join(here, 'autounattend.xml'), os.path.join(st, 'autounattend.xml'))
for f in sorted(os.listdir(os.path.join(here, 'setup'))):
    sub(os.path.join(here, 'setup', f), os.path.join(st, 'setup', f))
EOF
    # a previous run leaves it owned by libvirt-qemu; the directory is ours
    rm -f "$IMG/setup.iso"
    xorriso -as mkisofs -quiet -J -R -V ZLSETUP -o "$IMG/setup.iso" "$st" 2> /dev/null
    chmod 0600 "$IMG/setup.iso"
    rm -rf "$st"
}

cmd_golden() {
    refuse_when_busy
    [ "${1:-}" = "--force" ] || [ ! -e "$IMG/golden.qcow2" ] || die "golden.qcow2 exists (golden --force rebuilds it)"
    cmd_init
    [ "$(state)" = "running" ] && die "zlwin is running (winvm.sh down)"
    [ -r "$IMG/win11.iso" ] || die "no $IMG/win11.iso -- download the Windows 11 x64 ISO (multi-edition) from https://www.microsoft.com/software-download/windows11 and install -m 0644 it there ($DOC)"
    cmd_mkiso
    rm -f "$IMG/golden.qcow2" "$IMG/run.qcow2" "$STATE/known_hosts"
    qemu-img create -q -f qcow2 "$IMG/golden.qcow2" "${DISK_GB}G"
    cp "$OVMF_VARS" "$IMG/run_VARS.fd"
    define "$IMG/golden.qcow2" "$IMG/win11.iso" "$IMG/virtio-win.iso" "$IMG/setup.iso"
    V start "$DOM" > /dev/null
    # the Windows ISO waits for a key ("Press any key to boot from CD or DVD")
    for _ in $(seq 1 25); do V send-key "$DOM" KEY_ENTER > /dev/null 2>&1 || true; sleep 1; done
    echo "golden: installing unattended; it powers off when done (watch it: $DOC, 'Watching the VM')"
    wait_off 14400 || die "the install did not power off within 4 hours -- look at its screen ($DOC)"
    cp "$IMG/run_VARS.fd" "$IMG/golden_VARS.fd"
    define "$IMG/golden.qcow2"
    echo "golden OK; check it: winvm.sh up && winvm.sh ssh cat /c/zl/setup.failed /c/zl/setup.log"
}

cmd_up() {
    refuse_when_busy
    [ -f "$STATE/ssh_config" ] || die "no state in $STATE -- winvm.sh init (or golden) first"
    [ -e "$IMG/golden.qcow2" ] || die "no golden image -- winvm.sh golden ($DOC)"
    if [ "$(state)" != "running" ]; then
        rm -f "$IMG/run.qcow2"
        qemu-img create -q -f qcow2 -b "$IMG/golden.qcow2" -F qcow2 "$IMG/run.qcow2"
        cp "$IMG/golden_VARS.fd" "$IMG/run_VARS.fd"
        define "$IMG/run.qcow2"
        V start "$DOM" > /dev/null
    fi
    wait_ssh 300 || die "no ssh to zl@$IP within 5 minutes -- look at its screen ($DOC)"
    echo "up OK: zlwin at $IP"
}

cmd_down() {
    [ "$(state)" = "running" ] || { echo "down: not running"; return 0; }
    V shutdown "$DOM" > /dev/null || true
    wait_off 180 || { V destroy "$DOM" > /dev/null; echo "down: forced off after 3 minutes"; }
    echo "down OK"
}

cmd_update_golden() {
    refuse_when_busy
    [ "$(state)" = "running" ] && die "zlwin is running (winvm.sh down first)"
    cp "$IMG/golden_VARS.fd" "$IMG/run_VARS.fd"
    define "$IMG/golden.qcow2"
    V start "$DOM" > /dev/null
    wait_ssh 300 || die "no ssh to the golden image"
    local ps='powershell -NoProfile -ExecutionPolicy Bypass -File C:/zl/setup/update.ps1' pass rc
    for pass in 1 2 3 4; do
        rc=0; ssh_zl "$ps -Phase windows" || rc=$?
        [ $rc -eq 0 ] && break
        [ $rc -eq 10 ] || die "update.ps1 -Phase windows failed (exit $rc); golden left running"
        echo "update-golden: reboot after pass $pass"
        ssh_zl 'shutdown -r -t 0' || true
        sleep 60
        wait_ssh 900 || die "no ssh after the update reboot"
    done
    ssh_zl "$ps -Phase tools" || die "update.ps1 -Phase tools failed; golden left running"
    ssh_zl 'shutdown -s -t 0' || true
    wait_off 1800 || die "golden did not power off after updating"
    cp "$IMG/run_VARS.fd" "$IMG/golden_VARS.fd"
    echo "update-golden OK; confirm with: winvm.sh ci"
}

cmd_ci() {
    cmd_up
    local rc=0
    make -C "$ZL" ci-windows WINDOWS_SSH=zlwin WINDOWS_SSH_FLAGS="-F $STATE/ssh_config" || rc=$?
    cmd_down
    return $rc
}

case "${1:-}" in
    init) cmd_init ;;
    golden) shift; cmd_golden "$@" ;;
    up) cmd_up ;;
    down) cmd_down ;;
    status) echo "zlwin: $(state)" ;;
    ssh) shift; exec ssh -F "$STATE/ssh_config" zlwin "$@" ;;
    ci) cmd_ci ;;
    update-golden) cmd_update_golden ;;
    screenshot)
        [ -n "${2:-}" ] || die "usage: winvm.sh screenshot <out.png>"
        raw="$STATE/screen.raw"
        V screenshot "$DOM" "$raw" > /dev/null
        python3 -I -c 'import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2])' "$raw" "$2" 2> /dev/null \
            || { mv "$raw" "${2%.png}.ppm"; echo "(no python3-pil: wrote ${2%.png}.ppm)"; exit 0; }
        rm -f "$raw"; echo "$2" ;;
    console) exec virt-viewer -c qemu:///system --attach "$DOM" ;;
    rdp) exec remmina -c "rdp://zl@$IP" ;;
    *) sed -n '2,23p' "$0"; exit 2 ;;
esac
