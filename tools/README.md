# tools

Programs a developer runs to regenerate files the repository commits. Nothing
in a build of `zc`, `zl` or `zls`, and nothing in `make test`, runs them: what
they write is committed, and a ci guard regenerates it into the build
directory and compares, so a stale file fails `make ci` rather than passing
unnoticed.

Each tool is a zerolang program in its own directory, one unit per file,
built by an `out/<tool>` rule in the Makefile and linted, formatted and
complexity-checked with the rest of the tree.

Each tool's directory says what it writes, the `make` target that regenerates
it, and the guard that checks it.
