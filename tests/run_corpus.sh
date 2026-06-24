#!/bin/bash
# run_corpus.sh -- Python-free golden-corpus test gate for the ported zerolang compiler.
#
# Three case kinds, each comparing the PORT compiler's behavior to a committed golden:
#   run    examples/ + emitc_corpus/ listed in run_cases.txt: port emit -> gcc -> run;
#          stdout must match run_golden/<name>.out and exit match run_golden/<name>.exit (0 default).
#          run_golden/<name>.args (one arg per line) supplies argv.
#   leak   every buildable example + corpus program: ASan build, run detect_leaks=1; 0 bytes
#          leaked and no use-after-free/double-free. KNOWN_LEAKY allowlists the shrinking set.
#   error  tests/fixtures/errors/<name>.z (invalid source): the compiler must exit non-zero and
#          print an error matching errors/<name>.err (code+message; volatile location dropped).
#          KNOWN_NOERR lists cases the PORT cannot yet catch (it has no error reporting) -> xfail.
#
# The gate is Python-free: it needs only the port `zc` + gcc (+ ASan). The reference (zc.py) is
# used ONLY by `--update` to (re)generate goldens offline, never by the gate.
#
# Usage:
#   bash tests/run_corpus.sh            # gate (builds the port zc via zc.py if $ZC unset)
#   ZC=/path/to/zc bash tests/run_corpus.sh
#   bash tests/run_corpus.sh --update   # regenerate run/.out + error/.err goldens from the reference
#   bash tests/run_corpus.sh --report   # list every case's status, never fail
set -u

MODE=check
case "${1:-}" in --update) MODE=update;; --report) MODE=report;; esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SYS="$ROOT/lib/system"
ERRORS="$ROOT/tests/fixtures/errors"
RUNGOLD="$ROOT/tests/fixtures/run_golden"
RUNCASES="$ROOT/tests/fixtures/run_cases.txt"
CF=(-std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter
    -Werror=implicit-function-declaration -Werror=implicit-int
    -Werror=int-conversion -Werror=incompatible-pointer-types)
ACF=(-fsanitize=address -g -O0 -std=c17 -w)
cd "$ROOT" || exit 9

# Ratchets. The corpus is fully leak-clean; KNOWN_LEAKY allowlists any known
# leaker and the gate forces removal once an entry is clean -- keep it empty.
KNOWN_LEAKY=""
# Negative cases the PORT cannot yet catch (it has no error collection/reporting; that is the
# Python reference only). They are xfail until the compiler's error reporting is ported. As each
# is fixed, remove it here -- the gate forces this (a now-passing entry fails with "remove ...").
KNOWN_NOERR=""
# Examples the PORT emitter cannot yet compile to valid C (the Python reference can, except
# genmath). Each is removed as its codegen gap is fixed; the gate fails on any UNEXPECTED
# non-build and on any entry here that now builds (forcing removal -- the ratchet).
KNOWN_NOBUILD=""
# Generic library units (no `main`, parameterized by a generic type) are
# instantiated by other units and cannot be compiled standalone -- not even by
# the Python reference. Excluded from the standalone-build gate entirely.
SKIP_BUILD="genmath"

is_in() { case " $2 " in *" $1 "*) return 0;; *) return 1;; esac; }

# Build the port zc once (the only Python touch -- builds the port, does not gate).
ZC="${ZC:-}"
if [ -z "$ZC" ]; then
  TZC="$(mktemp -d)"; ZC="$TZC/zc"
  echo "[bootstrap: reference builds the port zc (~2.5min)]"
  uv run python src/zc.py zc --src src -o "$ZC.c" || { echo "FAIL: reference emit of zc"; exit 2; }
  gcc "${CF[@]}" -o "$ZC" "$ZC.c" || { echo "FAIL: gcc build of port zc"; exit 2; }
fi

D=$(mktemp -d); trap 'rm -rf "$D" "${TZC:-}"' EXIT
fails=0; pass=0; leakclean=0; xleak=0; xfail=0 xnobuild=0 skip=0

norm_err() {  # keep the stable contract: error code+message lines + the count summary
  grep -aE '^error\[E[0-9]+\]:|[0-9]+ error(s)? found' "$1" 2>/dev/null
}

argv_of() { local u="$1"; [ -f "$RUNGOLD/$u.args" ] && tr '\n' '\0' < "$RUNGOLD/$u.args"; }

############################ RUN cases ############################
do_run() {
  [ -f "$RUNCASES" ] || return
  while read -r name dir; do
    [ -z "$name" ] && continue
    local cc="$ZC" src="$dir"
    if [ "$MODE" = update ]; then cc="ref"; fi
    if [ "$cc" = ref ]; then
      uv run python src/zc.py "$name" --src "$dir" -o "$D/$name.c" 2>"$D/$name.e" || { echo "REFEMIT-FAIL $name"; continue; }
    else
      "$ZC" "$name" --src "$dir" --system "$SYS" --emit-c "$D/$name.c" 2>"$D/$name.e" || { [ "$MODE" = report ] && echo "skip(emit) $name"; skip=$((skip+1)); continue; }
    fi
    gcc "${CF[@]}" -o "$D/$name.bin" "$D/$name.c" 2>"$D/$name.g" || { [ "$MODE" = report ] && echo "skip(build) $name"; skip=$((skip+1)); continue; }
    local rd; rd=$(mktemp -d "$D/r.XXXX")
    local A=(); [ -f "$RUNGOLD/$name.args" ] && mapfile -t A < "$RUNGOLD/$name.args"
    ( cd "$rd" && "$D/$name.bin" "${A[@]}" >"$D/$name.out" 2>/dev/null </dev/null ); local ec=$?
    if [ "$MODE" = update ]; then
      mkdir -p "$RUNGOLD"; cp "$D/$name.out" "$RUNGOLD/$name.out"
      if [ "$ec" != 0 ]; then echo "$ec" > "$RUNGOLD/$name.exit"; else rm -f "$RUNGOLD/$name.exit"; fi
      echo "updated $name (exit $ec)"; continue
    fi
    local gold="$RUNGOLD/$name.out" exp=0
    [ -f "$RUNGOLD/$name.exit" ] && exp=$(cat "$RUNGOLD/$name.exit")
    [ -f "$gold" ] || { skip=$((skip+1)); continue; }
    if diff -q "$gold" "$D/$name.out" >/dev/null 2>&1 && [ "$ec" = "$exp" ]; then
      pass=$((pass+1))
    else
      echo "FAIL(run) $name (exit got=$ec exp=$exp)"; [ "$ec" = "$exp" ] && diff "$gold" "$D/$name.out" | head -4; fails=$((fails+1))
    fi
  done < "$RUNCASES"
}

############################ LEAK cases ############################
do_leak() {
  [ "$MODE" = update ] && return
  local f name dir
  for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .z); dir=$(dirname "$f")
    is_in "$name" "$SKIP_BUILD" && continue
    if ! "$ZC" "$name" --src "$dir" --system "$SYS" --emit-c "$D/L_$name.c" 2>"$D/L_$name.ce"; then
      if is_in "$name" "$KNOWN_NOBUILD"; then [ "$MODE" = report ] && echo "xnobuild(emit) $name"; xnobuild=$((xnobuild+1)); continue
      else echo "FAIL(build) $name emit: $(grep -am1 . "$D/L_$name.ce")"; fails=$((fails+1)); continue; fi
    fi
    if ! gcc "${ACF[@]}" -o "$D/L_$name.bin" "$D/L_$name.c" 2>"$D/L_$name.cg"; then
      if is_in "$name" "$KNOWN_NOBUILD"; then [ "$MODE" = report ] && echo "xnobuild(gcc) $name"; xnobuild=$((xnobuild+1)); continue
      else echo "FAIL(build) $name gcc: $(grep -am1 'error:' "$D/L_$name.cg")"; fails=$((fails+1)); continue; fi
    fi
    if is_in "$name" "$KNOWN_NOBUILD"; then echo "FAIL(ratchet) $name now builds -- remove from KNOWN_NOBUILD"; fails=$((fails+1)); continue; fi
    local rd; rd=$(mktemp -d "$D/lr.XXXX")
    local A=(); [ -f "$RUNGOLD/$name.args" ] && mapfile -t A < "$RUNGOLD/$name.args"
    ( cd "$rd" && ASAN_OPTIONS=detect_leaks=1 timeout 60 "$D/L_$name.bin" "${A[@]}" >/dev/null 2>"$D/L_$name.err" </dev/null )
    if grep -aqE "ERROR: AddressSanitizer: (heap-use-after-free|attempting double-free|heap-buffer-overflow|stack-)" "$D/L_$name.err"; then
      echo "FAIL(unsafe) $name $(grep -aoE 'ERROR: AddressSanitizer: [a-z-]+' "$D/L_$name.err" | head -1)"; fails=$((fails+1)); continue
    fi
    if grep -aqE "SUMMARY: AddressSanitizer: [0-9]+ byte" "$D/L_$name.err"; then
      if is_in "$name" "$KNOWN_LEAKY"; then [ "$MODE" = report ] && echo "xleak $name"; xleak=$((xleak+1));
      else echo "FAIL(leak) $name $(grep -aoE '[0-9]+ byte' "$D/L_$name.err"|head -1) (not in KNOWN_LEAKY)"; fails=$((fails+1)); fi
    else
      if is_in "$name" "$KNOWN_LEAKY"; then echo "FAIL(ratchet) $name now leak-CLEAN -- remove from KNOWN_LEAKY"; fails=$((fails+1));
      else leakclean=$((leakclean+1)); fi
    fi
  done
}

############################ ERROR (negative) cases ############################
do_error() {
  [ -d "$ERRORS" ] || return
  local f name
  for f in "$ERRORS"/*.z; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .z)
    if [ "$MODE" = update ]; then
      uv run python src/zc.py "$name" --src "$ERRORS" -o /dev/null 2>"$D/$name.re" >/dev/null
      norm_err "$D/$name.re" > "$ERRORS/$name.err"
      echo "updated error $name ($(wc -l < "$ERRORS/$name.err") lines)"; continue
    fi
    "$ZC" "$name" --src "$ERRORS" --system "$SYS" --emit-c /dev/null 2>"$D/$name.pe" >/dev/null; local ec=$?
    local gold="$ERRORS/$name.err" matches=0
    [ "$ec" != 0 ] && [ -f "$gold" ] && [ "$(norm_err "$D/$name.pe")" = "$(cat "$gold")" ] && matches=1
    if is_in "$name" "$KNOWN_NOERR"; then
      if [ "$matches" = 1 ]; then echo "FAIL(ratchet) $name now reports its error -- remove from KNOWN_NOERR"; fails=$((fails+1));
      else [ "$MODE" = report ] && echo "xfail(noerr) $name"; xfail=$((xfail+1)); fi
      continue
    fi
    if [ "$matches" = 1 ]; then pass=$((pass+1)); else echo "FAIL(error) $name (exit=$ec)"; fails=$((fails+1)); fi
  done
}

do_run
do_leak
do_error

echo "----"
echo "pass=$pass leak-clean=$leakclean xfail=$xfail xleak=$xleak xnobuild=$xnobuild skip=$skip fails=$fails"
[ "$MODE" = update ] && { echo "UPDATE DONE"; exit 0; }
[ "$MODE" = report ] && exit 0
[ $fails -eq 0 ] && { echo "CORPUS GATE GREEN"; exit 0; } || { echo "CORPUS GATE FAILED ($fails)"; exit 1; }
