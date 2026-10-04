# tools

Programs a developer runs to regenerate files the repository commits. Nothing
in a build of `zc`, `zl` or `zls`, and nothing in `make test`, runs them: what
they write is committed, and a ci guard regenerates it into the build
directory and compares, so a stale file fails `make ci` rather than passing
unnoticed.

Each tool is a zerolang program in its own directory, one unit per file,
built by an `out/<tool>` rule in the Makefile and linted, formatted and
complexity-checked with the rest of the tree.

| Tool | Writes | Regenerate | Check |
|---|---|---|---|
| [asmgen](asmgen/asmgen.z) | `src/runtime/natives/_Z_MATH_ARITH_<ARCH>.inc`, math's word-vector kernels as GNU inline asm | `make regen-math-asm` | `make math-asm-guard` |
