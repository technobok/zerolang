#!/bin/bash
# Self-host memory-safety + leak gate for the PORTED compiler (src/zemitterc.z).
#
# Builds the .z-emitted zc (stage2) with AddressSanitizer and runs THAT COMPILER
# over every example + corpus unit, in BOTH emit modes (--emit-c /dev/null and
# --full --dump-sql -), under detect_leaks=1. zc itself must be memory-safe (no
# use-after-free / double-free) and leak 0 bytes WHILE IT EMITS.
#
# This is distinct from tests/leakcheck.sh, which ASan-checks the EMITTED PROGRAM
# (it builds & runs each example binary). Here the COMPILER is the program under
# test: the bug class is .z-emitter ownership-invalidation gaps that only execute
# when the self-hosted zc compiles certain constructs (generators, facets, ...).
#
# KNOWN_LEAKY tracks the shrinking set whose self-emit still leaks: a leak there is
# tolerated, a leak elsewhere FAILS, and a KNOWN_LEAKY unit now clean ALSO fails
# (the ratchet). Any use-after-free / double-free FAILS unconditionally.
#
# Usage:
#   bash tests/selfhost_asan.sh                  # gate (bootstraps stage2-asan zc via compiler0)
#   STAGE1=/path/to/zc bash tests/selfhost_asan.sh         # reuse a compiler0-built stage1 (skip the ~3min emit)
#   ZC_ASAN=/path/to/zc_asan bash tests/selfhost_asan.sh   # reuse a prebuilt ASan stage2 zc
#   DL=0 bash tests/selfhost_asan.sh             # safety only (no leak detection)
#   bash tests/selfhost_asan.sh --report         # list every unit/mode status, never fail
set -u

REPORT=0
[ "${1:-}" = "--report" ] && REPORT=1

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYS="$ROOT/lib/system"
CFLAGS=(-std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter
        -Werror=implicit-function-declaration -Werror=implicit-int
        -Werror=int-conversion -Werror=incompatible-pointer-types)
ACFLAGS=(-fsanitize=address -g -O0 -std=c17 -w)
DL="${DL:-1}"          # ASAN detect_leaks (1 = leaks + safety; 0 = safety only)
cd "$ROOT" || exit 9

# Shrinking work queue: units whose self-emit still leaks (ratchet to empty).
KNOWN_LEAKY=""

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT

# --- bootstrap the ASan-instrumented stage2 (.z-emitted) zc ------------------
ZC_ASAN="${ZC_ASAN:-}"
if [ -z "$ZC_ASAN" ]; then
  STAGE1="${STAGE1:-}"
  if [ -z "$STAGE1" ]; then
    echo "[bootstrap: cc bootstrap/zc.c -> stage1 (the seed)]"
    gcc "${CFLAGS[@]}" -o "$T/stage1" bootstrap/zc.c \
      || { echo "FAIL: cc of bootstrap seed"; exit 2; }
    STAGE1="$T/stage1"
  fi
  echo "[bootstrap: stage1 emits stage2; gcc -fsanitize=address]"
  "$STAGE1" zc --src src --system "$SYS" --emit-c "$T/stage2.c" \
    || { echo "FAIL: stage1 emit of stage2"; exit 2; }
  gcc "${ACFLAGS[@]}" -o "$T/zc_asan" "$T/stage2.c" \
    || { echo "FAIL: ASan gcc build of stage2"; exit 2; }
  ZC_ASAN="$T/zc_asan"
fi

D=$(mktemp -d "$T/run.XXXX")
fails=0; clean=0; expleak=0; skip=0; seen_leaky=""

is_known() { case " $KNOWN_LEAKY " in *" $1 "*) return 0;; *) return 1;; esac; }

run_mode() { # <unit> <srcdir> <emitc|dumpsql>
  local u="$1" dir="$2" mode="$3" err rc sig fr n
  local args
  if [ "$mode" = emitc ]; then args=(--emit-c /dev/null); else args=(--full --dump-sql -); fi
  err="$D/$u.$mode.err"
  ASAN_OPTIONS=detect_leaks=$DL timeout 120 "$ZC_ASAN" "$u" --src "$dir" --system "$SYS" \
      "${args[@]}" >/dev/null 2>"$err" </dev/null
  rc=$?
  # Hard memory error (UAF / double-free / overflow / SEGV) -- LeakSanitizer uses a
  # distinct "ERROR: LeakSanitizer:" prefix, so this matches only AddressSanitizer.
  if grep -aqE "ERROR: AddressSanitizer:" "$err"; then
    sig=$(grep -aoE "ERROR: AddressSanitizer: [A-Za-z-]+" "$err" | head -1)
    fr=$(grep -aoE "z_t[0-9]+_[A-Za-z0-9_]+" "$err" | head -1)
    echo "FAIL(unsafe)  $u/$mode  ${sig#ERROR: AddressSanitizer: }  ${fr:-?}"; fails=$((fails+1)); return
  fi
  if grep -aqE "byte\(s\) leaked|ERROR: LeakSanitizer:" "$err"; then
    n=$(grep -aoE "[0-9]+ byte\(s\) leaked" "$err" | head -1 | grep -oE "^[0-9]+")
    fr=$(grep -aoE "z_t[0-9]+_[A-Za-z0-9_]+" "$err" | head -1)
    if is_known "$u"; then
      seen_leaky="$seen_leaky $u"
      [ $REPORT -eq 1 ] && echo "xleak         $u/$mode  ${n}B (known)"; expleak=$((expleak+1))
    else
      echo "FAIL(leak)    $u/$mode  ${n}B  ${fr:-?}"; fails=$((fails+1))
    fi
    return
  fi
  if [ $rc -ne 0 ]; then
    [ $REPORT -eq 1 ] && echo "skip(gap)     $u/$mode  rc=$rc"; skip=$((skip+1)); return
  fi
  [ $REPORT -eq 1 ] && echo "clean         $u/$mode"; clean=$((clean+1))
}

check_one() { run_mode "$1" "$2" emitc; run_mode "$1" "$2" dumpsql; }

for f in examples/*.z;                    do [ -e "$f" ] && check_one "$(basename "$f" .z)" examples; done
for f in tests/fixtures/emitc_corpus/*.z; do [ -e "$f" ] && check_one "$(basename "$f" .z)" tests/fixtures/emitc_corpus; done

# ratchet: a KNOWN_LEAKY unit that never leaked this run must be removed
for k in $KNOWN_LEAKY; do
  case " $seen_leaky " in
    *" $k "*) ;;
    *) echo "FAIL(ratchet) $k now CLEAN -- remove from KNOWN_LEAKY"; fails=$((fails+1));;
  esac
done

echo "----"
echo "clean=$clean known-leak=$expleak skipped=$skip fails=$fails"
[ $REPORT -eq 1 ] && exit 0
[ $fails -eq 0 ] && { echo "SELFHOST ASAN GATE GREEN"; exit 0; } || { echo "SELFHOST ASAN GATE FAILED ($fails)"; exit 1; }
