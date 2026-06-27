# bootstrap — the committed C bootstrap seed

`bootstrap/zc.c` is a **generated, committed C dump of the self-hosted Zero
compiler**. It is the Python-free way to build the compiler from a clean
checkout: a C toolchain is the only requirement.

```sh
cc -std=c17 -o zc bootstrap/zc.c      # 'zc' is now the self-hosted compiler
./zc <unit> --src src --system lib/system --emit-c out.c
```

`cc bootstrap/zc.c` *is* the compiler because the seed is the compiler's own
self-emitted C, which is byte-stable: building the project with it reproduces the
same `zc.c` (the self-hosting fixpoint — see `tests/test_fixedpoint.py`). This is
the Zig/OCaml model (a committed, periodically-refreshed compiler artifact),
chosen over a tag-chain replay or a platform-specific binary.

## It is a *recent* seed, not necessarily the current compiler

The seed is **not regenerated every commit.** A normal change builds current
`src` with the existing seed and leaves `bootstrap/zc.c` untouched. The seed only
needs to be able to *compile* current `src`; the resulting compiler is the
current one regardless of the seed's age.

Regenerate it (`make bump-seed`) only when:
- the seed can no longer build `main` — i.e. the compiler's own source started
  using a language feature the seed's compiler doesn't understand; or
- periodic hygiene.

## Validation

`make test-bootstrap` proves the seed bootstraps a correct compiler with no
Python: it `cc`s the seed, double-bootstraps to the byte-identical self-host
fixpoint, and compiles a locked-in unit to its smoke golden. It also reports
whether the seed is current or has lagged (a hint to run `make bump-seed`).

This file is a build artifact, not hand-edited source. Do not edit `zc.c` by
hand — change `src/*.z` and run `make bump-seed`.
