#!/usr/bin/env bash
# Emit a deterministic snapshot of generated C for the whole corpus into <outdir>.
# Used as the byte-diff oracle for the emitter cname-purity refactor: capture a
# baseline, re-run after each phase, `diff -r` the two trees. A zero diff proves
# the conversion was output-identical.
#
# Usage: tools/emit_snapshot.sh <outdir>
set -u
outdir="${1:?usage: emit_snapshot.sh <outdir>}"
cd "$(dirname "$0")/.."
mkdir -p "$outdir"

# Self-hosted compiler units (multi-unit; exercise cross-unit emission).
for u in zlexer zvfs zast; do
  uv run python src/zc.py "$u" --src src -o "$outdir/$u.c" 2>"$outdir/$u.err" \
    || echo "FAIL zc $u (see $u.err)"
done

# All example programs (single-unit + file-unit deps).
for f in examples/*.z; do
  name="$(basename "$f" .z)"
  uv run python src/zc.py "$name" --src examples -o "$outdir/ex_$name.c" \
    2>"$outdir/ex_$name.err" || echo "FAIL zc example $name"
done

# Drop empty .err files so `diff -r` stays quiet on success.
find "$outdir" -name '*.err' -empty -delete
echo "snapshot -> $outdir : $(ls "$outdir"/*.c 2>/dev/null | wc -l) C files"
