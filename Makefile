ZC       := uv run python src/zc.py
CC       := gcc
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types
BUILDDIR := out

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: check test test-clang test-all test-fast test-verbose test-emitter test-typecheck test-parser test-infra test-lf fmt build clean bootstrap-lint

# Patterns that complicate bootstrapping the compiler in zerolang.
# Each new violation must be reviewed — do not increase the baseline counts.
BOOTSTRAP_MSG := "  [bootstrap-lint] These Python-specific patterns complicate future self-hosting."
BOOTSTRAP_MSG2 := "  Do not introduce new uses. Run 'make bootstrap-lint' to check."

check:
	uv run ruff format src/ tests/
	uv run ruff check src/ tests/ --fix
	uv run ty check src/
	@$(MAKE) --no-print-directory bootstrap-lint

# Baseline counts of existing violations (update when migrating away)
# isinstance:0  comprehension:8  lambda:0  try/except:8  hasattr:6
# getattr:4 (F2 — defensive duck-typing on heterogeneous unions)
# name-compare:14 (Phase 7e — cross-structure .name ==/!= in src/*.py)
# startswith:42 (F3 — string-prefix tests; prefer id-based dispatch)
# name-literal-compare:270 (F3/F4 — buckets A/B/C done, D deferred)
# emitter-name-resolution:19 (typed-AST-authoritative — emitter reads stamps, not names; drive to 0)
bootstrap-lint:
	@fail=0; \
	count=$$(grep -rn 'isinstance(' src/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: isinstance() usage increased ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'isinstance(' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn '\[.*\bfor\b.*\bin\b' src/*.py | wc -l); \
	if [ $$count -gt 8 ]; then \
		echo "ERROR: list comprehension usage increased ($$count > 8 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\[.*\bfor\b.*\bin\b' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'lambda ' src/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: lambda usage increased ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'lambda ' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn -E '^\s*(try:|except\b)' src/*.py | wc -l); \
	if [ $$count -gt 8 ]; then \
		echo "ERROR: try/except usage increased ($$count > 8 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn -E '^\s*(try:|except\b)' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn '\byield\b' src/*.py | wc -l); \
	if [ $$count -gt 80 ]; then \
		echo "ERROR: yield usage found ($$count > 80 baseline)"; \
		echo "  Note: the baseline accounts for the 'yield' keyword in"; \
		echo "  the Zerolang lexer/parser/AST/error messages, not Python yield."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\byield\b' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'hasattr(' src/*.py | wc -l); \
	if [ $$count -gt 6 ]; then \
		echo "ERROR: hasattr() usage increased ($$count > 6 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'hasattr(' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'getattr(' src/*.py | wc -l); \
	if [ $$count -gt 4 ]; then \
		echo "ERROR: getattr() usage increased ($$count > 4 baseline)"; \
		echo "  F2: prefer direct attribute access; for genuinely"; \
		echo "  heterogeneous unions, narrow with nodetype/type() and cast()."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'getattr(' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rnE '\.name (==|!=) [a-zA-Z_][a-zA-Z_0-9]*\.name' src/*.py | grep -v 'ztc-string-compare-ok' | wc -l); \
	if [ $$count -gt 14 ]; then \
		echo "ERROR: cross-structure .name comparisons increased ($$count > 14 baseline)"; \
		echo "  Phase 7e: compare by id (nodeid/entry_id/variableid) instead."; \
		echo "  Intentional string compare? Add '# ztc-string-compare-ok: <reason>' on the same line."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rnE '\.name (==|!=) [a-zA-Z_][a-zA-Z_0-9]*\.name' src/*.py | grep -v 'ztc-string-compare-ok' | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'startswith(' src/*.py | wc -l); \
	if [ $$count -gt 42 ]; then \
		echo "ERROR: startswith() usage increased ($$count > 42 baseline)"; \
		echo "  F3: string-prefix tests are bootstrap-hostile; prefer"; \
		echo "  id-based dispatch (BuiltinName / nodeid / name_id)."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'startswith(' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rnE '(==|!=) *"[A-Za-z_][A-Za-z0-9_]*"' src/*.py | grep -v 'ztc-string-compare-ok' | wc -l); \
	if [ $$count -gt 270 ]; then \
		echo "ERROR: literal name compares increased ($$count > 270 baseline)"; \
		echo "  F3/F4: compare by id (BuiltinName / nodeid / name_id) instead."; \
		echo "  Intentional? Add '# ztc-string-compare-ok: <reason>' on the same line."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rnE '(==|!=) *"[A-Za-z_][A-Za-z0-9_]*"' src/*.py | grep -v 'ztc-string-compare-ok' | tail -5; fail=1; \
	fi; \
	count=$$(grep -nE 'Optional.*ZType.*= field' src/ztypes.py | wc -l); \
	if [ $$count -gt 5 ]; then \
		echo "ERROR: Optional[ZType] field declarations on ZType increased ($$count > 5 baseline)"; \
		echo "  Use id-form cross-refs (parent_id / type_id) and resolve via _type_by_id()."; \
		echo "  This mirrors the Phase 7 ZScope/Entry/Unit pattern and keeps the type graph SQL-friendly."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE 'Optional.*ZType.*= field' src/ztypes.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -cE '_(resolved_type|typetype_of)\(' src/zemitterc.py); \
	if [ $$count -gt 19 ]; then \
		echo "ERROR: emitter name-resolution calls increased ($$count > 19 baseline)"; \
		echo "  The typed AST is authoritative: read the typecheck stamp"; \
		echo "  (node_type / *_type_id) instead of re-resolving by name with"; \
		echo "  _resolved_type / _typetype_of. Drive this baseline to 0."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE '_(resolved_type|typetype_of)\(' src/zemitterc.py | tail -5; fail=1; \
	fi; \
	if [ $$fail -eq 0 ]; then echo "bootstrap-lint: OK"; fi; \
	exit $$fail

# Full suite, parallelized via pytest-xdist (uses all cores).
# Run before every commit.
test:
	uv run python -m pytest tests/ -n auto

# Same suite, but the emitter tests compile generated C with clang
# instead of gcc. Catches warnings/errors that gcc tolerates but clang
# doesn't (real bugs surface from this in practice — e.g. const-discard
# on pointer args, sign-compare differences). Run before pushing when
# changing emitter or runtime code; CI should run this in parallel
# with `make test`.
test-clang:
	Z_TEST_CC=clang $(MAKE) --no-print-directory test

# Convenience: run both gcc and clang test passes sequentially.
test-all: test test-clang

# Inner-loop dev: skip emitter tests (gcc per-test is the bulk of the time).
# Use during development; `make test` covers the full suite before commit.
test-fast:
	uv run python -m pytest tests/ --ignore=tests/test_emitter.py -n auto

# Sequential full suite, with verbose output. For debugging test failures
# where xdist makes output hard to read.
test-verbose:
	uv run python -m pytest tests/ -v

# Marker-based subsets — see pyproject.toml [tool.pytest.ini_options].markers.
# Useful when you've changed only one compiler stage and want a tighter loop.
test-emitter:
	uv run python -m pytest tests/ -m emitter -n auto

test-typecheck:
	uv run python -m pytest tests/ -m typecheck -n auto

test-parser:
	uv run python -m pytest tests/ -m parser -n auto

test-infra:
	uv run python -m pytest tests/ -m infra -n auto

# Re-run only tests that failed in the previous run.
test-lf:
	uv run python -m pytest tests/ --lf -n auto

fmt:
	uv run ruff format src/ tests/

# compile all examples: .z -> .c -> binary
build:
	@mkdir -p $(BUILDDIR)
	@ok=0; fail=0; \
	for name in $(NAMES); do \
		$(ZC) $$name --src examples -o $(BUILDDIR)/$$name.c 2>/dev/null; \
		if [ $$? -ne 0 ]; then \
			echo "FAIL zc   $$name"; fail=$$((fail+1)); continue; \
		fi; \
		$(CC) $(CFLAGS) -o $(BUILDDIR)/$$name $(BUILDDIR)/$$name.c 2>/dev/null; \
		if [ $$? -ne 0 ]; then \
			echo "FAIL gcc  $$name"; fail=$$((fail+1)); continue; \
		fi; \
		echo "OK        $$name"; ok=$$((ok+1)); \
	done; \
	echo ""; \
	echo "$$ok passed, $$fail failed ($(BUILDDIR)/)"

clean:
	rm -rf $(BUILDDIR)
