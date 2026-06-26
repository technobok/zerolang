# compiler0 — the original Python reference compiler

This directory holds **compiler0**: the Python implementation of the Zero
language compiler. It is the compiler that *bootstrapped* the self-hosted
compiler, which is written in Zero itself and lives in `../src/*.z`.

## This is not how you build the current compiler

To build the real (self-hosted) compiler, use the `.z` sources:

```sh
make bin/zc          # build the self-hosted compiler
bin/zc <unit> --src src
```

compiler0 is **not** the build path for day-to-day work. It is kept here for two
reasons:

1. **Bootstrap + oracle (current role).** While compiler0 is still maintained in
   parallel, it is the stage0 that builds `src/zc.z` (see the `ZC` variable in
   the `Makefile` and `_ZC` in `tests/conftest.py`), and it is the *differential
   oracle*: the `*_differential` tests compile the same `.z` source through both
   compilers and require identical output. This cross-check catches typechecker
   and emitter drift in the self-hosted compiler.
2. **History / pedagogy (eventual role).** It is the readable record of how the
   language was first implemented.

## It shares the live language runtime

compiler0 does **not** carry its own copy of the C runtime fragments. It reads
the canonical runtime — the `.c.tmpl` templates and `.inc` native fragments —
from the shared `../src/runtime/`, the same files the self-hosted compiler reads
(`zemitterc_runtime._RUNTIME_DIR` / `zemitterc_templates._TEMPLATE_DIR` point
there). A single source of truth keeps the two emitters byte-identical, which is
exactly what the differential gate requires.

## After the freeze

When the parallel-maintenance window ends, compiler0 will be **frozen** (we stop
updating it). At that point it becomes a read-only historical artifact and is
**no longer guaranteed to compile or emit correctly**: the self-hosted compiler
keeps evolving the language and the shared `../src/runtime/` templates, and a
frozen compiler0 may not understand newer `src/*.z` sources or match an evolved
runtime. To preserve it as a self-consistent, runnable snapshot, its
then-current `src/runtime/` should be copied into `compiler0/runtime/` at freeze
time and its `_RUNTIME_DIR` / `_TEMPLATE_DIR` repointed back to its own copy.

The frozen compiler0 is the historical root of the bootstrap chain; the living,
Python-free bootstrap of the current compiler is a committed `bootstrap/zc.c`
seed (see `doc/bootstrap.pdoc`), not this directory.
