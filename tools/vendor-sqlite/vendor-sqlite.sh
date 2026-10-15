#!/usr/bin/env bash
# vendor-sqlite.sh VERSION YEAR SHA3_256 -- replace vendor/sqlite with SQLite's
# amalgamation VERSION (3.54.0), released in YEAR (sqlite.org files each release
# under its year), after checking the zip against the SHA3-256 that
# https://sqlite.org/download.html publishes for it. Copies sqlite3.c and
# sqlite3.h only -- the shell and the extension header are not built here.
# Run from the repository root, then update vendor/sqlite/VERSION.md and run
# `make ci`: a new SQLite is one commit. Downloads go to a temporary directory
# that is removed on exit; nothing is executed from them.
set -eu
VERSION=${1:?version, e.g. 3.54.0}; YEAR=${2:?release year, e.g. 2026}; WANT=${3:?SHA3-256 of the zip}
IFS=. read -r MAJ MIN PAT <<< "$VERSION"
NUM=$(printf '%d%02d%02d00' "$MAJ" "$MIN" "$PAT")
ZIP="sqlite-amalgamation-$NUM.zip"
test -d vendor/sqlite || { echo "vendor-sqlite.sh: run from the repository root" >&2; exit 2; }
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
curl -sSfL -o "$T/$ZIP" "https://sqlite.org/$YEAR/$ZIP"
GOT=$(python3 -I -c 'import hashlib,sys; print(hashlib.sha3_256(open(sys.argv[1],"rb").read()).hexdigest())' "$T/$ZIP")
if [ "$GOT" != "$WANT" ]; then
  echo "vendor-sqlite.sh: $ZIP has SHA3-256 $GOT, want $WANT -- not vendored" >&2; exit 1
fi
mkdir "$T/x"; unzip -q "$T/$ZIP" -d "$T/x"
D="$T/x/sqlite-amalgamation-$NUM"
grep -q "define SQLITE_VERSION *\"$VERSION\"" "$D/sqlite3.h" \
  || { echo "vendor-sqlite.sh: $ZIP's sqlite3.h is not version $VERSION" >&2; exit 1; }
cp "$D/sqlite3.c" "$D/sqlite3.h" vendor/sqlite/
echo "vendored SQLite $VERSION ($ZIP, SHA3-256 $GOT) -- update vendor/sqlite/VERSION.md"
