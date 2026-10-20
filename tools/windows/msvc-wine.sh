#!/usr/bin/env bash
# msvc-wine.sh -- install MSVC (cl, link, the Windows SDK) to run under wine, for
# `make test-msvc-wine`, with msvc-wine (https://github.com/mstorsjo/msvc-wine)
# at a pinned commit. It downloads from Microsoft, which needs the Visual Studio
# Build Tools licence accepted: pass --accept-license to say you accept it.
#
# usage: msvc-wine.sh --accept-license DEST
#   then: make test-msvc-wine MSVC_WINE=DEST/msvc/bin/x64
set -euo pipefail

MSVC_WINE_REPO=https://github.com/mstorsjo/msvc-wine.git
MSVC_WINE_COMMIT=514f8ea34842cd6d831804d0e9658d3a32870ae1
APT="sudo apt install wine msitools winbind python3 git"

die() { echo "msvc-wine.sh: $*" >&2; exit 1; }

[ "${1:-}" = "--accept-license" ] || die "usage: msvc-wine.sh --accept-license DEST (you accept Microsoft's Visual Studio Build Tools licence)"
[ -n "${2:-}" ] || die "usage: msvc-wine.sh --accept-license DEST"
DEST=$(mkdir -p "$2" && cd "$2" && pwd)

miss=""
for c in wine msiextract python3 git; do command -v "$c" > /dev/null 2>&1 || miss+=" $c"; done
[ -z "$miss" ] || die "missing:$miss -- $APT"
command -v ntlm_auth > /dev/null 2>&1 || die "missing winbind (cl's /Zi needs it) -- $APT"

if [ ! -d "$DEST/src/.git" ]; then git clone -q "$MSVC_WINE_REPO" "$DEST/src"; fi
git -C "$DEST/src" fetch -q origin "$MSVC_WINE_COMMIT" 2> /dev/null || true
git -C "$DEST/src" checkout -q --detach "$MSVC_WINE_COMMIT"

if [ ! -x "$DEST/msvc/bin/x64/cl" ]; then
    python3 -I "$DEST/src/vsdownload.py" --accept-license --dest "$DEST/msvc"
    "$DEST/src/install.sh" "$DEST/msvc"
fi
[ -x "$DEST/msvc/bin/x64/cl" ] || die "no $DEST/msvc/bin/x64/cl after the install -- see its output above"
echo "msvc-wine OK: make test-msvc-wine MSVC_WINE=$DEST/msvc/bin/x64"
