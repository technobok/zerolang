#!/bin/bash
# Memory-leak gate for the ported C emitter (src/zemitterc.z).
#
# Every buildable example + corpus program is emitted to C by the PORTED zc, built
# with AddressSanitizer, and run under detect_leaks=1; it must leak 0 bytes and be
# memory-safe. The KNOWN_LEAKY allowlist (below) tracks the shrinking set of
# programs that still leak: a leak there is tolerated, a leak anywhere else FAILS,
# and a KNOWN_LEAKY program that has become clean ALSO fails (forcing its removal --
# the ratchet). Any use-after-free / double-free FAILS unconditionally.
#
# This gate is Python-free at its core: "0 bytes leaked" needs only the port zc +
# gcc + ASan + the example source. The reference (zc.py) is the per-construct oracle
# only while fixing (tools/probe_emitc.py), never here. Building the port zc still
# bootstraps once via zc.py until a committed bootstrap binary exists.
#
# Usage:
#   bash tests/leakcheck.sh                 # gate (builds the port zc via zc.py)
#   ZC=/path/to/zc bash tests/leakcheck.sh  # gate with a prebuilt port zc
#   bash tests/leakcheck.sh --report        # list every program's status, never fail
set -u

REPORT=0
[ "${1:-}" = "--report" ] && REPORT=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYS="$ROOT/lib/system"
ACFLAGS=(-fsanitize=address -g -O0 -std=c17 -w)
cd "$ROOT" || exit 9

# The shrinking work queue: programs the PORT still leaks (the reference is clean).
# Remove a name when its leak is fixed -- the gate enforces this (a clean
# KNOWN_LEAKY program fails with "remove from KNOWN_LEAKY"). Keep in sync with
# KNOWN_LEAKY in tests/test_emitc_leak_z.py.
KNOWN_LEAKY=""

ZC="${ZC:-}"
if [ -z "$ZC" ]; then
  TZC="$(mktemp -d)"
  ZC="$TZC/zc"
  echo "[bootstrap: cc bootstrap/zc.c -> port zc]"
  gcc -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
      -Werror=implicit-function-declaration -Werror=implicit-int \
      -Werror=int-conversion -Werror=incompatible-pointer-types \
      -o "$ZC" bootstrap/zc.c || { echo "FAIL: cc of bootstrap seed"; exit 2; }
fi

D=$(mktemp -d)
trap 'rm -rf "$D" "${TZC:-}"' EXIT
fails=0; clean=0; expleak=0; skip=0

is_known() { case " $KNOWN_LEAKY " in *" $1 "*) return 0;; *) return 1;; esac; }

check_one() { # <unit> <srcdir>
  local u="$1" dir="$2"
  if ! "$ZC" "$u" --src "$dir" --system "$SYS" --emit-c "$D/$u.c" 2>"$D/$u.emit.err"; then
    [ $REPORT -eq 1 ] && echo "skip(emit)  $u"; skip=$((skip+1)); return
  fi
  if ! gcc "${ACFLAGS[@]}" -o "$D/$u.bin" "$D/$u.c" 2>"$D/$u.gcc.err"; then
    [ $REPORT -eq 1 ] && echo "skip(build) $u"; skip=$((skip+1)); return
  fi
  local rd; rd=$(mktemp -d "$D/run.XXXX")
  ( cd "$rd" && ASAN_OPTIONS=detect_leaks=1 timeout 60 "$D/$u.bin" >/dev/null 2>"$D/$u.run.err" </dev/null )
  if grep -aqE "ERROR: AddressSanitizer: (heap-use-after-free|attempting double-free|heap-buffer-overflow|stack-)" "$D/$u.run.err"; then
    local b; b=$(grep -aoE "ERROR: AddressSanitizer: [a-z-]+" "$D/$u.run.err" | head -1)
    echo "FAIL(unsafe)  $u  $b"; fails=$((fails+1)); return
  fi
  if grep -aqE "SUMMARY: AddressSanitizer: [0-9]+ byte" "$D/$u.run.err"; then
    local n; n=$(grep -aE "SUMMARY: AddressSanitizer" "$D/$u.run.err" | head -1 | grep -oE "[0-9]+ byte" | grep -oE "[0-9]+")
    if is_known "$u"; then
      [ $REPORT -eq 1 ] && echo "xleak       $u  ${n}B (known)"; expleak=$((expleak+1))
    else
      echo "FAIL(leak)    $u  ${n}B (not in KNOWN_LEAKY)"; fails=$((fails+1))
    fi
  else
    if is_known "$u"; then
      echo "FAIL(ratchet) $u now CLEAN -- remove from KNOWN_LEAKY"; fails=$((fails+1))
    else
      [ $REPORT -eq 1 ] && echo "clean       $u"; clean=$((clean+1))
    fi
  fi
}

for f in examples/*.z; do [ -e "$f" ] && check_one "$(basename "$f" .z)" examples; done
for f in tests/fixtures/emitc_corpus/*.z; do [ -e "$f" ] && check_one "$(basename "$f" .z)" tests/fixtures/emitc_corpus; done

echo "----"
echo "clean=$clean known-leak=$expleak skipped=$skip fails=$fails"
[ $REPORT -eq 1 ] && exit 0
[ $fails -eq 0 ] && { echo "LEAK GATE GREEN"; exit 0; } || { echo "LEAK GATE FAILED ($fails)"; exit 1; }
