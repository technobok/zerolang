# testgen

Python scripts that write math's table-driven test fixtures. Their output is
committed under `tests/fixtures/emitc_corpus/math/`; nothing in a build or in
`make test` / `make ci` runs them, and the tree builds and tests with a C
toolchain alone. Run one only to change what a fixture covers, then review the
diff, run the program for its new golden, and commit both.

They are Python, not zerolang, on purpose: the expected values must come from
an implementation independent of the one under test. Zerolang's only
arbitrary-precision arithmetic is `math` itself, so a zerolang generator would
compute the goldens with the code they check, and a bug would pass. Python's
integers, `fractions.Fraction` and `math.isqrt` are that independent oracle,
and two of the scripts take their tables from Go's own math/big tests.

Python 3 and its standard library only (run with 3.14; the syntax parses as far back as 3.8). The fixture-writing scripts
format their output with `bin/zl`, so build it first (`make bin/zl`).

| Script | Writes | Oracle |
|---|---|---|
| `kerneltables.py [ROOT]` | `math_kernel_shapes.z` | Python ints |
| `mathtables.py OPS` | rows for `math_bigint_tables.z` (`add,sub,mul`), `math_bigint_division.z` (`div,mod`), `math_bigint_text.z` (`text`), on stdout | Python ints |
| `fixedtables.py [ROOT [OUT]]` | `math_fixedint.z` | Python ints mod 2^(64N) |
| `decimaltables.py [ROOT [OUT]]` | `math_decimal.z` | Python ints, `Fraction` |
| `fixedfloattables.py [ROOT [OUT]]` | `math_fixedfloat.z` | `Fraction`, `isqrt` |
| `rattables.py OUT.z` | `math_bigrat.z` | Go's rat tests, `Fraction` |
| `floattables.py OUTDIR` | `math_bigfloat.z`, `math_bigfloat_text.z` | Go's float tests, `Fraction` |

`ROOT` defaults to the repository the script sits in. `rattables.py` and
`floattables.py` read Go's test tables with `git show`, from the clone of
https://go.googlesource.com/go that `GO_GIT` names, at the commit math/big was
ported from (577b91f341).

Each script reproduces its committed fixture byte for byte; `mathtables.py`'s
rows appear verbatim in theirs.
