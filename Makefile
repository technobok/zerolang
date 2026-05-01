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

.PHONY: check test test-fast test-verbose test-emitter test-typecheck test-parser test-infra test-lf fmt build clean bootstrap-lint

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
# isinstance:0  comprehension:14  lambda:0  try/except:8  hasattr:16
# getattr:4 (F2 — defensive duck-typing on heterogeneous unions)
# name-compare:14 (Phase 7e — cross-structure .name ==/!= in src/*.py)
bootstrap-lint:
	@fail=0; \
	count=$$(grep -rn 'isinstance(' src/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: isinstance() usage increased ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'isinstance(' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn '\[.*\bfor\b.*\bin\b' src/*.py | wc -l); \
	if [ $$count -gt 14 ]; then \
		echo "ERROR: list comprehension usage increased ($$count > 14 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\[.*\bfor\b.*\bin\b' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'lambda ' src/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: lambda usage increased ($$count > 1 baseline)"; \
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
	if [ $$count -gt 0 ]; then \
		echo "ERROR: yield usage found ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\byield\b' src/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'hasattr(' src/*.py | wc -l); \
	if [ $$count -gt 16 ]; then \
		echo "ERROR: hasattr() usage increased ($$count > 16 baseline)"; \
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
	if [ $$fail -eq 0 ]; then echo "bootstrap-lint: OK"; fi; \
	exit $$fail

# Full suite, parallelized via pytest-xdist (uses all cores).
# Run before every commit.
test:
	uv run python -m pytest tests/ -n auto

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
