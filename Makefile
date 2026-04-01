ZC       := uv run python src/zc.py
CC       := gcc
CFLAGS   := -Wall -Wno-unused-function
BUILDDIR := out

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: check test fmt build clean bootstrap-lint

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
# isinstance:459  comprehension:14  lambda:0  try/except:8
bootstrap-lint:
	@fail=0; \
	count=$$(grep -rn 'isinstance(' src/*.py | wc -l); \
	if [ $$count -gt 459 ]; then \
		echo "ERROR: isinstance() usage increased ($$count > 459 baseline)"; \
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
	if [ $$fail -eq 0 ]; then echo "bootstrap-lint: OK"; fi; \
	exit $$fail

test:
	uv run python -m pytest tests/ -v

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
