# compiler0 — the original Python reference compiler (FROZEN)

> **⚠️ FROZEN — historical artifact.** compiler0 is no longer maintained and is
> **not** part of the build or test path. It was the Python implementation that
> *bootstrapped* the self-hosted compiler (written in Zero itself, in `../src/*.z`)
> and served as the differential test oracle during the port. Both roles are now
> retired: the build bootstraps from the committed `../bootstrap/zc.c` seed, and
> the tests compare the port against committed goldens. Frozen at the
> `python-stage0-final` tag. It may not compile current `src/*.z` and is kept only
> as the readable record of the language's first implementation.

## How to build the current compiler (not this directory)

```sh
make bin/zc          # self-hosted compiler, bootstrapped by bootstrap/zc.c (no Python)
bin/zc <unit> --src src --system lib/system
```

`cc bootstrap/zc.c` *is* the self-hosted compiler (the byte-stable self-emit; see
`../bootstrap/README.md`). compiler0 plays no part.

## Why it's still in the tree

History / pedagogy: it is the readable record of how the language was first
implemented, and the historical root of the bootstrap chain. It is *not* deleted
so the `python-stage0-final` snapshot stays browsable in `main` (the tag also
preserves it in history regardless).

Its former roles are gone:
- **Bootstrap** → superseded by `../bootstrap/zc.c` (`make` `ZC`/`tests/conftest.py`
  default to the seed; `BOOTSTRAP=python` / `Z_BOOTSTRAP=python` remain only as an
  opt-in escape hatch while the directory is present).
- **Differential oracle** → retired with the port-vs-reference tests (their durable
  Python-free replacements — corpus/golden/fixpoint/ASan gates — already cover the
  same ground).

## Runtime caveat

compiler0 carries no copy of the C runtime; it reads the canonical
`../src/runtime/` (the `.c.tmpl` templates and `.inc` native fragments) shared with
the self-hosted compiler. As the self-hosted compiler keeps evolving the language
and that runtime, a frozen compiler0 is **not guaranteed to compile or emit
correctly** against newer sources. To run it as a self-consistent snapshot, copy
the then-current `src/runtime/` into `compiler0/runtime/` and repoint its
`_RUNTIME_DIR` / `_TEMPLATE_DIR` — but for the current source it still reads
`../src/runtime/` directly.
