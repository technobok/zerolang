ZC       := uv run python compiler0/zc.py
CC       := gcc
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types
BUILDDIR := out

# install tree (GOROOT-style). Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: check test test-clang test-all test-fast test-verbose test-emitter test-typecheck test-parser test-infra test-leak leakcheck selfhost-asan test-corpus test-corpus-z ci test-lf fmt build clean bootstrap-lint style-lint style-lint-fast zc install regen-goldens bump-seed test-bootstrap

# Patterns that complicate bootstrapping the compiler in zerolang.
# Each new violation must be reviewed — do not increase the baseline counts.
BOOTSTRAP_MSG := "  [bootstrap-lint] These Python-specific patterns complicate future self-hosting."
BOOTSTRAP_MSG2 := "  Do not introduce new uses. Run 'make bootstrap-lint' to check."

check:
	uv run ruff format compiler0/ tests/
	uv run ruff check compiler0/ tests/ --fix
	uv run ty check compiler0/
	@$(MAKE) --no-print-directory bootstrap-lint
	@$(MAKE) --no-print-directory style-lint-fast

# Style ratchets over src/*.z (tools/lint_style.py), pinned at 0.
# style-lint-fast is the parse-only empty-clause + first-arg-elision check (fast; runs in `check`).
# style-lint adds the typecheck-based redundant-suffix check and the re-parse
# -verified unneeded-paren check (~minutes; run pre-push). See doc/styleguide.pdoc
# "Literal Type Inference" / "Empty Clauses" / "Parentheses".
style-lint-fast:
	uv run python tools/lint_style.py --empty-only --check --check-elide --check-for-while

style-lint:
	uv run python tools/lint_style.py --check --check-elide --check-parens --check-for-while

# Baseline counts of existing violations (update when migrating away)
# isinstance:0  comprehension:8  lambda:0  try/except:8  hasattr:6
# getattr:4 (F2 — defensive duck-typing on heterogeneous unions)
# name-compare:14 (Phase 7e — cross-structure .name ==/!= in compiler0/*.py)
# startswith:42 (F3 — string-prefix tests; prefer id-based dispatch)
# name-literal-compare:270 (F3/F4 — buckets A/B/C done, D deferred)
# emitter-name-resolution:0 (typed-AST-authoritative — emitter reads stamps, not names; achieved)
# emitter-z-literal:0 (emitter generates no inline z_{ identifiers; reads stored cnames; achieved)
# emitter-name-mangler:0 (no local _mangle_func/_mangle_var; shared mangle_*_name only; achieved)
bootstrap-lint:
	@fail=0; \
	count=$$(grep -rn 'isinstance(' compiler0/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: isinstance() usage increased ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'isinstance(' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn '\[.*\bfor\b.*\bin\b' compiler0/*.py | wc -l); \
	if [ $$count -gt 8 ]; then \
		echo "ERROR: list comprehension usage increased ($$count > 8 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\[.*\bfor\b.*\bin\b' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'lambda ' compiler0/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: lambda usage increased ($$count > 0 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'lambda ' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn -E '^\s*(try:|except\b)' compiler0/*.py | wc -l); \
	if [ $$count -gt 8 ]; then \
		echo "ERROR: try/except usage increased ($$count > 8 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn -E '^\s*(try:|except\b)' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn '\byield\b' compiler0/*.py | wc -l); \
	if [ $$count -gt 80 ]; then \
		echo "ERROR: yield usage found ($$count > 80 baseline)"; \
		echo "  Note: the baseline accounts for the 'yield' keyword in"; \
		echo "  the Zerolang lexer/parser/AST/error messages, not Python yield."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn '\byield\b' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'hasattr(' compiler0/*.py | wc -l); \
	if [ $$count -gt 6 ]; then \
		echo "ERROR: hasattr() usage increased ($$count > 6 baseline)"; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'hasattr(' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'getattr(' compiler0/*.py | wc -l); \
	if [ $$count -gt 4 ]; then \
		echo "ERROR: getattr() usage increased ($$count > 4 baseline)"; \
		echo "  F2: prefer direct attribute access; for genuinely"; \
		echo "  heterogeneous unions, narrow with nodetype/type() and cast()."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'getattr(' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rnE '\.name (==|!=) [a-zA-Z_][a-zA-Z_0-9]*\.name' compiler0/*.py | grep -v 'ztc-string-compare-ok' | wc -l); \
	if [ $$count -gt 14 ]; then \
		echo "ERROR: cross-structure .name comparisons increased ($$count > 14 baseline)"; \
		echo "  Phase 7e: compare by id (nodeid/entry_id/variableid) instead."; \
		echo "  Intentional string compare? Add '# ztc-string-compare-ok: <reason>' on the same line."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rnE '\.name (==|!=) [a-zA-Z_][a-zA-Z_0-9]*\.name' compiler0/*.py | grep -v 'ztc-string-compare-ok' | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'startswith(' compiler0/*.py | wc -l); \
	if [ $$count -gt 42 ]; then \
		echo "ERROR: startswith() usage increased ($$count > 42 baseline)"; \
		echo "  F3: string-prefix tests are bootstrap-hostile; prefer"; \
		echo "  id-based dispatch (BuiltinName / nodeid / name_id)."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'startswith(' compiler0/*.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rnE '(==|!=) *"[A-Za-z_][A-Za-z0-9_]*"' compiler0/*.py | grep -v 'ztc-string-compare-ok' | wc -l); \
	if [ $$count -gt 270 ]; then \
		echo "ERROR: literal name compares increased ($$count > 270 baseline)"; \
		echo "  F3/F4: compare by id (BuiltinName / nodeid / name_id) instead."; \
		echo "  Intentional? Add '# ztc-string-compare-ok: <reason>' on the same line."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rnE '(==|!=) *"[A-Za-z_][A-Za-z0-9_]*"' compiler0/*.py | grep -v 'ztc-string-compare-ok' | tail -5; fail=1; \
	fi; \
	count=$$(grep -nE 'Optional.*ZType.*= field' compiler0/ztypes.py | wc -l); \
	if [ $$count -gt 5 ]; then \
		echo "ERROR: Optional[ZType] field declarations on ZType increased ($$count > 5 baseline)"; \
		echo "  Use id-form cross-refs (parent_id / type_id) and resolve via _type_by_id()."; \
		echo "  This mirrors the Phase 7 ZScope/Entry/Unit pattern and keeps the type graph SQL-friendly."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE 'Optional.*ZType.*= field' compiler0/ztypes.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -cE '_(resolved_type|typetype_of)\(' compiler0/zemitterc.py); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: emitter name-resolution calls increased ($$count > 0 baseline)"; \
		echo "  The typed AST is authoritative: read the typecheck stamp"; \
		echo "  (node_type / *_type_id) instead of re-resolving by name with"; \
		echo "  _resolved_type / _typetype_of. Drive this baseline to 0."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE '_(resolved_type|typetype_of)\(' compiler0/zemitterc.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -cE 'z_\{' compiler0/zemitterc.py); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: inline z_{ identifier derivations increased ($$count > 0 baseline)"; \
		echo "  The emitter generates NO C names: read ztype.cname / cname_base /"; \
		echo "  variable_cname / the ZConformance entity (or compose from cname_base)."; \
		echo "  Shared mangle_func_name / mangle_var_name cover the name-string residual."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE 'z_\{' compiler0/zemitterc.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -cE '_mangle_func\(|_mangle_var\(|_mangle_callable\(' compiler0/zemitterc.py); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: emitter-local name manglers increased ($$count > 0 baseline)"; \
		echo "  The emitter has no local _mangle_func/_mangle_var/_mangle_callable."; \
		echo "  Read the stored cname; shared ztypes.mangle_func_name / mangle_var_name"; \
		echo "  (no leading underscore, so allowed) cover the name-string residual."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -nE '_mangle_func\(|_mangle_var\(|_mangle_callable\(' compiler0/zemitterc.py | tail -5; fail=1; \
	fi; \
	count=$$(grep -rn 'object\.__setattr__' compiler0/*.py | wc -l); \
	if [ $$count -gt 0 ]; then \
		echo "ERROR: object.__setattr__ usage increased ($$count > 0 baseline)"; \
		echo "  Frozen AST nodes are immutable: mint a fresh node and rebind a"; \
		echo "  parent dict/list entry instead of mutating a frozen field."; \
		echo $(BOOTSTRAP_MSG); echo $(BOOTSTRAP_MSG2); \
		grep -rn 'object\.__setattr__' compiler0/*.py | tail -5; fail=1; \
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

# Native (self-hosted src/*.z) compiler tests only -- the port's gates (corpus,
# fixpoint, differentials, leak) without the Python-reference suite. Run when
# changing src/*.z.
test-native:
	uv run python -m pytest tests/ -m native -n auto

# Python-reference (compiler0/*.py) tests only -- retires with the reference compiler.
# Run when changing compiler0/*.py.
test-py:
	uv run python -m pytest tests/ -m "not native" -n auto

# Memory-leak gate: every buildable example + corpus program emitted by the
# ported zc, built with ASan, run under detect_leaks=1; 0 bytes leaked
# (KNOWN_LEAKY ratchets). Slow (one ASan binary per program) -- deliberately
# NOT in `make test`. `leakcheck` runs the Python-free shell runner directly.
test-leak:
	uv run python -m pytest tests/test_emitc_leak_z.py -n auto

leakcheck:
	bash tests/leakcheck.sh

# Self-host memory-safety + leak gate: the .z-emitted zc, built with ASan, must
# compile every example + corpus unit (both --emit-c and --full --dump-sql modes)
# with no use-after-free / double-free and 0 bytes leaked. Checks the COMPILER
# while it emits, not the emitted program. Slow -- deliberately NOT in `make test`.
selfhost-asan:
	bash tests/selfhost_asan.sh

# Unified Python-free corpus gate: behavioral (.out stdout/exit goldens) + leak
# (detect_leaks=1) + negative (.err error goldens) for every case, comparing the
# ported zc to committed goldens (no Python at gate time). `--update` regenerates
# goldens from the reference. Slow; NOT in `make test`.
test-corpus:
	bash tests/run_corpus.sh

# Self-hosted analogue of test-corpus: the ported zc builds the corpus
# runner (src/ztestrunner.z), which then drives the same 3-kind gate via
# os.spawn (no shell, no Python at gate time) and reproduces run_corpus.sh's
# tally. Slow; NOT in `make test`. run_corpus.sh stays the CI gate.
test-corpus-z:
	@mkdir -p $(BUILDDIR)
	$(ZC) zc --src src -o $(BUILDDIR)/zc.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc $(BUILDDIR)/zc.c
	$(BUILDDIR)/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c
	$(BUILDDIR)/ztestrunner --zc $(BUILDDIR)/zc --cc $(CC) --root .

# ci -- the consolidated gate, runnable in one command. Runs the style/lint
# check, the full suite (test, which builds + runs the port-vs-reference
# differentials and the fixpoint -- exercising BOTH compilers), the Python-free
# seed bootstrap (test-bootstrap), the self-host ASan gate (selfhost-asan), and
# the behavioral + leak + negative corpus goldens (test-corpus, which already
# includes the detect_leaks=1 leak kind). Sequential sub-makes so a parallel
# `make -j` can't run the heavy suites concurrently and OOM a small box.
ci:
	$(MAKE) --no-print-directory check
	$(MAKE) --no-print-directory test
	$(MAKE) --no-print-directory test-bootstrap
	$(MAKE) --no-print-directory selfhost-asan
	$(MAKE) --no-print-directory test-corpus
	@echo "CI GATE GREEN: check + test (both compilers) + bootstrap + selfhost-asan + corpus"

# Re-run only tests that failed in the previous run.
test-lf:
	uv run python -m pytest tests/ --lf -n auto

fmt:
	uv run ruff format compiler0/ tests/

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

# bin/zc -- the self-hosted compiler, bootstrapped by the reference (zc.py).
# Persistent + git-ignored; rebuilt when the compiler sources change. The dev
# bin/zc self-locates to this repo (lib/system here; runtime falls back to
# src/runtime, as the dev tree has no lib/runtime).
bin/zc: $(wildcard src/*.z) $(wildcard compiler0/*.py) $(wildcard lib/system/*.z)
	@mkdir -p bin
	$(ZC) zc --src src -o bin/zc.c
	$(CC) $(CFLAGS) -o bin/zc bin/zc.c

# zc -- convenience alias for bin/zc.
zc: bin/zc

# Standalone dump binaries, built by the ported compiler (bin/zc). out/zlexer
# emits the canonical token dump; out/zparser emits the canonical AST dump in
# both single-file (out/zparser <file>) and whole-program (out/zparser --program
# <dir> main) modes. These are the Python-free regeneration path for the
# lexer/parser/program goldens -- the dumper logic lives in src/zlexer.z and
# src/zparser.z, not in any compiler0/*.py oracle.
out/zlexer: bin/zc $(wildcard src/zlexer.z) $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer --src src --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c

out/zparser: bin/zc $(wildcard src/zparser.z) $(wildcard src/zlexer.z) $(wildcard src/zast.z) $(wildcard src/zvfs.z) $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser --src src --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c

# Regenerate the lexer / parser / whole-program goldens from the .z dump
# binaries (no Python). Iterates every examples/*.z (matching the differential
# tests), so it includes the main-less modules the build target's SKIP omits.
# Always review the resulting diff before committing -- a non-empty diff means
# the dump output changed.
regen-goldens: out/zlexer out/zparser
	@for f in examples/*.z; do \
		name=$$(basename $$f .z); \
		$(BUILDDIR)/zlexer $$f > tests/fixtures/lexer_golden/$$name.tokens; \
		$(BUILDDIR)/zparser $$f > tests/fixtures/parser_golden/$$name.ast; \
	done
	@for d in tests/fixtures/parser_program/*.tree; do \
		name=$$(basename $$d .tree); \
		$(BUILDDIR)/zparser --program $$d main > tests/fixtures/parser_program/$$name.expected; \
	done
	@echo "regenerated lexer/parser/program goldens via $(BUILDDIR)/zlexer + $(BUILDDIR)/zparser"

# bootstrap/zc.c -- the committed, Python-free bootstrap seed: a self-emitted,
# self-reproducing C dump of the compiler. `cc bootstrap/zc.c` IS the
# self-hosted compiler. See bootstrap/README.md and doc/bootstrap.pdoc.
#
# bump-seed regenerates it from a fresh bin/zc (uses compiler0 to build bin/zc
# while the reference is live; post-freeze a seed-built bin/zc does the same).
# Run only when test-bootstrap reports the seed can no longer build main, or for
# periodic hygiene -- NOT every commit.
bump-seed: bin/zc
	bin/zc zc --src src --system lib/system --emit-c bootstrap/zc.c
	@echo "regenerated bootstrap/zc.c -- review the diff and commit"

# test-bootstrap -- prove the committed seed bootstraps a correct compiler with
# NO Python: cc the seed, then double-bootstrap and assert the self-host fixpoint
# (b2 == b3, lag-tolerant) plus a correctness check (a seed-built compiler builds
# ztypes to its smoke golden). Slow (3 zc.c compiles); NOT in `make test`.
test-bootstrap:
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-seed bootstrap/zc.c
	$(BUILDDIR)/zc-seed zc --src src --system lib/system --emit-c $(BUILDDIR)/b1.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b1 $(BUILDDIR)/b1.c
	$(BUILDDIR)/zc-b1 zc --src src --system lib/system --emit-c $(BUILDDIR)/b2.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b2 $(BUILDDIR)/b2.c
	$(BUILDDIR)/zc-b2 zc --src src --system lib/system --emit-c $(BUILDDIR)/b3.c
	@diff $(BUILDDIR)/b2.c $(BUILDDIR)/b3.c \
		&& echo "fixpoint OK (b2 == b3)" \
		|| { echo "FAIL: seed-built compiler does not converge"; exit 1; }
	@cmp -s $(BUILDDIR)/b1.c bootstrap/zc.c \
		&& echo "seed is current (b1 == committed seed)" \
		|| echo "note: seed has lagged (b1 != committed seed) -- run 'make bump-seed' when convenient"
	$(BUILDDIR)/zc-b1 ztypes --src src --system lib/system --emit-c $(BUILDDIR)/zt.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zt $(BUILDDIR)/zt.c
	$(BUILDDIR)/zt | diff - tests/fixtures/ztypes_z/smoke.expected \
		&& echo "correctness OK (seed-built zc compiles ztypes to golden)"
	@echo "bootstrap seed OK: 'cc bootstrap/zc.c' builds a correct self-hosting zc (no Python)"

# install -- a self-contained tree at $(ROOT) + a $(BINDIR)/zc symlink. The
# runtime ships as lib/runtime (copied from src/runtime). os.exePath resolves
# the symlink to $(ROOT)/bin/zc, so the installed zc self-locates the tree.
install: bin/zc
	mkdir -p $(ROOT)/bin $(ROOT)/lib $(BINDIR)
	cp bin/zc $(ROOT)/bin/zc
	rm -rf $(ROOT)/lib/system $(ROOT)/lib/runtime $(ROOT)/doc $(ROOT)/src
	cp -r lib/system $(ROOT)/lib/system
	cp -r src/runtime $(ROOT)/lib/runtime
	cp -r doc $(ROOT)/doc
	cp -r src $(ROOT)/src
	ln -sf $(ROOT)/bin/zc $(BINDIR)/zc
	@echo "installed zc -> $(BINDIR)/zc (tree: $(ROOT))"

clean:
	rm -rf $(BUILDDIR) bin
