# tools

Programs a developer runs to regenerate files the repository commits. Nothing
in a build of `zc`, `zl` or `zls`, and nothing in `make test`, runs them: what
they write is committed, and a ci guard regenerates it into the build
directory and compares, so a stale file fails `make ci` rather than passing
unnoticed.

Each tool lives in a directory of its own. A zerolang tool is one unit per
file, built by an `out/<tool>` rule in the Makefile and linted, formatted and
complexity-checked with the rest of the tree. testgen is Python, because its
job needs an arithmetic independent of the zerolang code it tests; its README
says why.

| Tool | Writes | Regenerate | Check |
|---|---|---|---|
| [asmgen](asmgen/asmgen.z) | `src/runtime/natives/_Z_MATH_ARITH_<ARCH>.inc`, math's word-vector kernels as GNU inline asm | `make regen-math-asm` | `make math-asm-guard` |
| [testgen](testgen/README.md) | math's table-driven fixtures under `tests/fixtures/emitc_corpus/math/`, their expected values from Python's exact arithmetic and Go's math/big tests | the script, by hand (testgen/README.md) | the fixtures are the corpus's own run cases |
