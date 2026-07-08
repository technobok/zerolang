CC       := gcc
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types
BUILDDIR := out

# Bootstrap compiler for building the .z sources: the committed, Python-free
# seed (bootstrap/zc.c -> $(BUILDDIR)/zc-seed; see bootstrap/README.md). A C
# toolchain is the only requirement to build and test zerolang.
ZC      := $(BUILDDIR)/zc-seed
ZC_DEP  := $(BUILDDIR)/zc-seed

# install tree (GOROOT-style). Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: check test ci build clean style-lint style-lint-fast zc zl install regen-goldens bump-seed test-bootstrap docs warn-check shadow-guard emitter-guard

# ZLSCOPE -- the files the zl gate lints/formats: the tool + compiler sources and the
# relocated front-end. The stdlib proper (io/os/collections/system/cli/core) is not gated
# (it carries pre-existing first-arg-elision labels that were never enforced).
ZLSCOPE := src/*.z lib/system/zlexer.z lib/system/zparser.z lib/system/zast.z lib/system/zvfs.z lib/system/tokenize.z lib/system/ast.z

# check -- the fast pre-commit gate: the parse/token/whitespace rules over the zl scope.
check: style-lint-fast

# Style gate, enforced by the self-hosted `zl` linter/formatter (src/zl.z). style-lint-fast is
# the fast tier (empty clauses, first-arg elision, for-while, trailing whitespace, final
# newline, colon and blank-line spacing); it runs in `check`. style-lint adds the typecheck-
# tier redundant-suffix rule and a formatter check (slower; run pre-push). See docs/zl.pdoc.
style-lint-fast: bin/zl
	bin/zl lint $(ZLSCOPE)

style-lint: bin/zl
	bin/zl lint --full --src src --system lib/system $(ZLSCOPE)
	bin/zl fmt --check $(ZLSCOPE)

# test -- build the compiler + the self-hosted test runner (src/ztestrunner.z),
# then run the fast corpus gate (run/leak/error/dump/smoke/differential kinds,
# all driven via os.spawn; no Python, no shell). Run before every commit.
test: bin/zc
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root .

# ci -- the consolidated gate, runnable in one command with only a C toolchain:
# the full style-lint, the heavy corpus gate (--heavy adds the self-host ASan +
# byte-identity fixpoint kinds to run/leak/error/dump/smoke/differential), and
# the Python-free seed bootstrap.
ci: bin/zc
	$(MAKE) --no-print-directory style-lint
	$(MAKE) --no-print-directory shadow-guard
	$(MAKE) --no-print-directory emitter-guard
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy
	$(MAKE) --no-print-directory test-bootstrap
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

# compile all examples: .z -> .c -> binary
build: bin/zc
	@mkdir -p $(BUILDDIR)
	@ok=0; fail=0; \
	for name in $(NAMES); do \
		bin/zc $$name --src examples --system lib/system --emit-c $(BUILDDIR)/$$name.c 2>/dev/null; \
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

# out/zc-seed -- the bootstrap compiler built from the committed, Python-free
# seed (bootstrap/zc.c). See bootstrap/README.md and `make test-bootstrap`.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c

# bin/zc -- the self-hosted compiler, bootstrapped by the seed. Persistent +
# git-ignored; rebuilt when the compiler sources change. The dev bin/zc
# self-locates to this repo (lib/system here; runtime falls back to src/runtime).
bin/zc: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(ZC_DEP)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system --emit-c bin/zc.c
	$(CC) $(CFLAGS) -o bin/zc bin/zc.c

# zc -- convenience alias for bin/zc.
zc: bin/zc

# bin/zl -- the zerolang linter + formatter (src/zl.z), built on the shared
# front-end via the compiler. A separate binary from zc so the compiler stays
# lean; zl links the front-end + typecheck (for --full's suffix rule), but never
# the emitter.
bin/zl: bin/zc $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z)
	@mkdir -p bin out
	bin/zc zl --src src --system lib/system --emit-c out/zl.c
	$(CC) $(CFLAGS) -o bin/zl out/zl.c

# zl -- convenience alias for bin/zl.
zl: bin/zl

# Standalone dump binaries (the Python-free golden regeneration path; the
# dumper logic lives in lib/system/zlexer.z and lib/system/zparser.z).
out/zlexer: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer --src src --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c

out/zparser: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser --src src --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c

# Regenerate the lexer / parser / whole-program goldens from the .z dump
# binaries (no Python). Always review the resulting diff before committing.
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

# bump-seed -- regenerate the committed seed from a fresh bin/zc. Run only when
# test-bootstrap reports the seed can no longer build main, or for hygiene.
bump-seed: bin/zc
	bin/zc zc --src src --system lib/system --emit-c bootstrap/zc.c
	@echo "regenerated bootstrap/zc.c -- review the diff and commit"

# test-bootstrap -- prove the committed seed bootstraps a correct compiler with
# NO Python: cc the seed, double-bootstrap and assert the fixpoint (b2 == b3),
# plus a correctness check (a seed-built compiler builds ztypes to its golden).
# Slow (3 zc.c compiles).
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

# install -- a self-contained tree at $(ROOT) + a $(BINDIR)/zc symlink.
install: bin/zc
	mkdir -p $(ROOT)/bin $(ROOT)/lib $(BINDIR)
	cp bin/zc $(ROOT)/bin/zc
	rm -rf $(ROOT)/lib/system $(ROOT)/lib/runtime $(ROOT)/docs $(ROOT)/src
	cp -r lib/system $(ROOT)/lib/system
	cp -r src/runtime $(ROOT)/lib/runtime
	cp -r docs $(ROOT)/docs
	cp -r src $(ROOT)/src
	ln -sf $(ROOT)/bin/zc $(BINDIR)/zc
	@echo "installed zc -> $(BINDIR)/zc (tree: $(ROOT))"

# docs -- render the .pdoc documentation to HTML. Commit the regenerated .html.
# Needs the picodoc renderer at ../picodoc-c/picodoc (see docs/Makefile).
docs:
	$(MAKE) -C docs
	@echo "rendered docs/ -- commit the regenerated .html"

# warn-check -- compile the emitted compiler C with every warning as an error.
warn-check: bin/zc
	$(CC) $(CFLAGS) -Werror -c bin/zc.c -o /dev/null
	@echo "warn-check OK: zero compiler warnings"

# shadow-guard -- ratchet against the user-shadow miscompile class. The C emitter
# must derive a type's C type from its canonical type id (typeRefC / scalarCTypeFor
# / cTypeForNameTid), never from the type NAME (cTypeOf / cTypeForName) directly --
# otherwise a user type shadowing a builtin scalar (i64: record {...}) emits the C
# scalar instead of its struct. The baselines pin the known-safe remaining by-name
# sites (numeric casts, userFnId-first dispatch, control-flow checks, and the
# head-gated assignment / fnSignature / typeRefC sites); a new by-name site grows
# the count and fails. New type emission must go through the id-based helpers.
shadow-guard:
	@n1=$$(grep -c 'cTypeOf name:' src/zemitterc.z); \
	n2=$$(grep -c 'cTypeForName symtab:' src/zemitterc.z); \
	fail=0; \
	if [ "$$n1" -gt 18 ]; then echo "shadow-guard FAIL: 'cTypeOf name:' = $$n1 (baseline 18)"; fail=1; fi; \
	if [ "$$n2" -gt 2 ]; then echo "shadow-guard FAIL: 'cTypeForName symtab:' = $$n2 (baseline 2)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new by-name C-type site was added. Resolve the C type from the canonical"; \
	  echo "  type id via scalarCTypeFor / cTypeForNameTid / typeRefC, not cTypeOf(name)."; \
	  echo "  (If a site was legitimately removed, lower the baseline here instead.)"; \
	  exit 1; \
	fi; \
	echo "shadow-guard OK: cTypeOf name:=$$n1 (<=18)  cTypeForName symtab:=$$n2 (<=2)"

# emitter-guard -- ratchet against name-resolution creep in the C emitter. The
# de-lookup arc drove these to their current floors: the emitter reads
# typechecker stamps and canonical ids; every remaining by-name resolution is a
# counted residual (template re-emission, probe-chain legs). A rising count
# means a new name-resolved site -- resolve from stamps/ids instead, or lower
# the baseline when a residual is legitimately removed.
emitter-guard:
	@e1=$$(grep -c 'ztypecheck.resolvedByKey' src/zemitterc.z); \
	e2=$$(grep -c 'ztypecheck.walkLookupTyperef' src/zemitterc.z); \
	e3=$$(grep -c 'resolveTypeIdByName' src/zemitterc.z); \
	e4=$$(grep -c 'userFnId' src/zemitterc.z); \
	e5=$$(grep -c 'childOwnershipText' src/zemitterc.z); \
	e6=$$(grep -c 'typeNameOfReg9' src/zemitterc.z); \
	e7=$$(grep -c 'ztypes.mangleVarName' src/zemitterc.z); \
	e8=$$(grep -cF 'io.readText' src/zemitterc.z); \
	e9=$$(grep -c 'monoOriginName' src/zemitterc.z); \
	fail=0; \
	if [ "$$e1" -gt 23 ]; then echo "emitter-guard FAIL: ztypecheck.resolvedByKey = $$e1 (baseline 23)"; fail=1; fi; \
	if [ "$$e2" -gt 5 ]; then echo "emitter-guard FAIL: ztypecheck.walkLookupTyperef = $$e2 (baseline 5)"; fail=1; fi; \
	if [ "$$e3" -gt 37 ]; then echo "emitter-guard FAIL: resolveTypeIdByName = $$e3 (baseline 37)"; fail=1; fi; \
	if [ "$$e4" -gt 39 ]; then echo "emitter-guard FAIL: userFnId = $$e4 (baseline 39)"; fail=1; fi; \
	if [ "$$e5" -gt 0 ]; then echo "emitter-guard FAIL: childOwnershipText = $$e5 (baseline 0)"; fail=1; fi; \
	if [ "$$e6" -gt 122 ]; then echo "emitter-guard FAIL: typeNameOfReg9 = $$e6 (baseline 122)"; fail=1; fi; \
	if [ "$$e7" -gt 22 ]; then echo "emitter-guard FAIL: ztypes.mangleVarName = $$e7 (baseline 22)"; fail=1; fi; \
	if [ "$$e8" -gt 5 ]; then echo "emitter-guard FAIL: io.readText = $$e8 (baseline 5)"; fail=1; fi; \
	if [ "$$e9" -gt 61 ]; then echo "emitter-guard FAIL: monoOriginName = $$e9 (baseline 61)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  reg.cnameOf instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: resolvedByKey=$$e1 walkLookup=$$e2 resolveByName=$$e3 userFnId=$$e4 ownText=$$e5 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 monoOrigin=$$e9"

clean:
	rm -rf $(BUILDDIR) bin
