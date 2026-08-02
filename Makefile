CC       := gcc
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types \
            -Werror=discarded-qualifiers

# Parallel by default: make fans out independent targets and the corpus runner
# fans out its per-case pipelines (--jobs). `make NPROC=1` forces everything
# serial (NPROC feeds both -j and the runner's --jobs).
NPROC    ?= $(shell nproc 2>/dev/null || echo 1)
MAKEFLAGS += -j$(NPROC)
# Daily-driver binaries only (bin/zc, bin/zl, bin/zls): light optimization
# makes self-compilation ~35% faster (1.30s -> 0.86s). -fwrapv and
# -fno-strict-aliasing pin down the C the emitter relies on. Bootstrap
# intermediates and the test runner stay -O0: they are built once and run
# once, so gcc time dominates.
OPTFLAGS := -O1 -fno-strict-aliasing -fwrapv
# Daily drivers also emit with the wyhash-style fast path for their own
# Map/Set dispatch (their inputs are trusted source trees). Everything
# else -- corpus, goldens, bootstrap fixpoint -- emits with the SipHash
# default; emitted C is byte-identical either way.
ZCHASH   := --fast-hash
# Daily drivers link the vendored mimalloc (vendor/mimalloc, one TU via
# src/static.c) ahead of libc so its malloc/free override glibc's:
# self-compile 0.93s -> 0.80s. `make MIMALLOC=0` builds pure-glibc
# drivers. Everything else (bootstrap intermediates, ztestrunner, corpus
# and user emission) stays glibc; the allocator never changes emitted C.
BUILDDIR := out
MIMALLOC ?= 1
ifeq ($(MIMALLOC),1)
MIMALLOC_OBJ := $(BUILDDIR)/mimalloc.o
else
MIMALLOC_OBJ :=
endif

# Bootstrap compiler for building the .z sources: the committed, Python-free
# seed (bootstrap/zc.c -> $(BUILDDIR)/zc-seed; see bootstrap/README.md). A C
# toolchain is the only requirement to build and test zerolang.
ZC      := $(BUILDDIR)/zc-seed
ZC_DEP  := $(BUILDDIR)/zc-seed

# install tree (GOROOT-style). Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath dissectlib
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: all check test ci ci-corpus build clean style-lint style-lint-fast zc zl zls install regen-goldens bump-seed test-bootstrap docs warn-check perf shadow-guard emitter-guard native-guard fallback-guard member-guard readable-check

# Keep pattern-chain intermediates (the per-example .c files) for debugging.
.SECONDARY:

# ZLSCOPE -- what the zl *linter* checks: the tool + compiler sources and the relocated
# front-end. The stdlib proper (io/os/collections/system/cli/core) is not linted (it carries
# pre-existing first-arg-elision labels that were never enforced).
ZLSCOPE := src/*.z lib/system/*.z
# FMTSCOPE -- what the zl *formatter* checks: fmt applies only whitespace/colon fixes (no
# elide-label issue), so it covers the whole codebase, keeping every .z consistently formatted.
FMTSCOPE := src/*.z lib/system/*.z examples/*.z

# all -- the default target: build the three tools (compiler, linter/formatter,
# language server). `make check` / `make test` are the gates; `make build` compiles
# the examples.
all: bin/zc bin/zl bin/zls

# check -- the fast pre-commit gate: the parse/token/whitespace rules, plus a repo-wide
# formatter check.
check: style-lint-fast

# Style gate, enforced by the self-hosted `zl` linter/formatter (src/zl.z). style-lint-fast is
# the fast tier (empty clauses, first-arg elision, for-while, trailing whitespace, final
# newline, colon and blank-line spacing) plus `zl fmt --check`; it runs in `check`. style-lint
# adds the typecheck-tier redundant-suffix rule (slower; run pre-push). See docs/zl.pdoc.
style-lint-fast: bin/zl
	bin/zl lint $(ZLSCOPE)
	bin/zl fmt --check $(FMTSCOPE)

style-lint: bin/zl
	bin/zl lint --full --src src --system lib/system $(ZLSCOPE)
	bin/zl fmt --check $(FMTSCOPE)

# out/ztestrunner -- the self-hosted corpus runner (src/ztestrunner.z), built
# on demand; test/ci run it with --jobs so per-case pipelines fan out (heavy
# kinds -- differential, selfhost-asan, fixpoint -- stay serial inside it).
$(BUILDDIR)/ztestrunner: bin/zc src/ztestrunner.z $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c -lquadmath -lm

# test -- build the compiler + the corpus runner, then run the fast corpus gate
# (run/leak/error/dump/smoke/differential kinds, all driven via os.spawn; no
# Python, no shell). Run before every commit.
test: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC)

# ci -- the consolidated gate, runnable in one command with only a C toolchain:
# the full style-lint, the heavy corpus gate (--heavy adds the self-host ASan +
# byte-identity fixpoint kinds to run/leak/error/dump/smoke/differential), and
# the Python-free seed bootstrap. The lint + guard + corpus phases are plain
# prerequisites so -j overlaps them; test-bootstrap stays last (and is
# internally serial -- b1 -> b2 -> b3 is a chain by nature).
ci: style-lint shadow-guard emitter-guard native-guard view-guard fallback-guard member-guard any-guard readable-check ci-corpus
	$(MAKE) --no-print-directory test-bootstrap
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

ci-corpus: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

# readable-check -- --readable-names is a debug affordance, so nothing else
# exercises it; without this it would rot unnoticed. The two schemes differ
# only in identifier spelling, so the built programs must behave identically.
readable-check: bin/zc
	@mkdir -p $(BUILDDIR)/rn
	@for n in hello vector records fibonacci; do \
	  bin/zc $$n --src examples --system lib/system --emit-c $(BUILDDIR)/rn/$$n-id.c || exit 1; \
	  bin/zc $$n --src examples --system lib/system --readable-names --emit-c $(BUILDDIR)/rn/$$n-rn.c || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-id $(BUILDDIR)/rn/$$n-id.c -lquadmath -lm || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-rn $(BUILDDIR)/rn/$$n-rn.c -lquadmath -lm || exit 1; \
	  $(BUILDDIR)/rn/$$n-id > $(BUILDDIR)/rn/$$n-id.out 2>&1; \
	  $(BUILDDIR)/rn/$$n-rn > $(BUILDDIR)/rn/$$n-rn.out 2>&1; \
	  diff -q $(BUILDDIR)/rn/$$n-id.out $(BUILDDIR)/rn/$$n-rn.out > /dev/null \
	    || { echo "readable-check FAIL: $$n differs between naming schemes"; exit 1; }; \
	done
	@echo "readable-check OK: --readable-names builds and runs identically"

# compile all examples: .z -> .c -> binary, one pattern-rule chain per example
# so -j fans out the emits and gcc's. Binaries land in $(BUILDDIR)/ex/.
EXDIR  := $(BUILDDIR)/ex
EXBINS := $(NAMES:%=$(EXDIR)/%.bin)

$(EXDIR)/%.c: examples/%.z bin/zc
	@mkdir -p $(EXDIR)
	bin/zc $* --src examples --system lib/system --emit-c $@

$(EXDIR)/%.bin: $(EXDIR)/%.c
	$(CC) $(CFLAGS) -o $@ $< -lquadmath -lm

build: $(EXBINS)
	@echo "$(words $(EXBINS)) examples built ($(EXDIR)/)"

# out/mimalloc.o -- the vendored allocator, one TU (own flags: third-party
# code is exempt from the project -Werror set). zc_tune.c is the option hook.
$(BUILDDIR)/mimalloc.o: vendor/mimalloc/src/static.c vendor/mimalloc/zc_tune.c $(wildcard vendor/mimalloc/src/*.c) $(wildcard vendor/mimalloc/include/*.h)
	@mkdir -p $(BUILDDIR)
	$(CC) -O2 -DNDEBUG -DMI_MALLOC_OVERRIDE -I vendor/mimalloc/include -c vendor/mimalloc/src/static.c -o $(BUILDDIR)/mimalloc-core.o
	$(CC) -O2 -DNDEBUG -I vendor/mimalloc/include -c vendor/mimalloc/zc_tune.c -o $(BUILDDIR)/mimalloc-tune.o
	ld -r $(BUILDDIR)/mimalloc-core.o $(BUILDDIR)/mimalloc-tune.o -o $@

# out/zc-seed -- the bootstrap compiler built from the committed, Python-free
# seed (bootstrap/zc.c). See bootstrap/README.md and `make test-bootstrap`.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c -lquadmath -lm

# bin/zc -- the self-hosted compiler, bootstrapped by the seed. Persistent +
# git-ignored; rebuilt when the compiler sources change. The dev bin/zc
# self-locates to this repo (lib/system here; runtime falls back to src/runtime).
bin/zc: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(ZC_DEP) $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -o bin/zc $(MIMALLOC_OBJ) bin/zc.c -lpthread -lquadmath -lm

# zc -- convenience alias for bin/zc.
zc: bin/zc

# bin/zl -- the zerolang linter + formatter (src/zl.z), built on the shared
# front-end via the compiler. A separate binary from zc so the compiler stays
# lean; zl links the front-end + typecheck (for --full's suffix rule), but never
# the emitter.
bin/zl: bin/zc $(MIMALLOC_OBJ) $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z)
	@mkdir -p bin out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -o bin/zl $(MIMALLOC_OBJ) out/zl.c -lpthread -lquadmath -lm

# bin/zls -- the zerolang language server (src/zls.z): JSON-RPC over
# stdio/--replay on the shared front-end via zcheck; no emitter. The
# lsp test kind in ztestrunner builds its own copy; this rule is the
# editor-facing binary.
bin/zls: bin/zc $(MIMALLOC_OBJ) $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z)
	@mkdir -p bin out
	bin/zc zls --src src --system lib/system $(ZCHASH) --emit-c out/zls.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -o bin/zls $(MIMALLOC_OBJ) out/zls.c -lpthread -lquadmath -lm

# zl -- convenience alias for bin/zl.
zl: bin/zl

# zls -- convenience alias for bin/zls.
zls: bin/zls

# Standalone dump binaries (the Python-free golden regeneration path; the
# dumper logic lives in lib/system/zlexer.z and lib/system/zparser.z).
out/zlexer: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer --src src --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c -lquadmath -lm

out/zparser: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser --src src --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c -lquadmath -lm

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
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-seed bootstrap/zc.c -lquadmath -lm
	$(BUILDDIR)/zc-seed zc --src src --system lib/system --emit-c $(BUILDDIR)/b1.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b1 $(BUILDDIR)/b1.c -lquadmath -lm
	$(BUILDDIR)/zc-b1 zc --src src --system lib/system --emit-c $(BUILDDIR)/b2.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b2 $(BUILDDIR)/b2.c -lquadmath -lm
	$(BUILDDIR)/zc-b2 zc --src src --system lib/system --emit-c $(BUILDDIR)/b3.c
	@diff $(BUILDDIR)/b2.c $(BUILDDIR)/b3.c \
		&& echo "fixpoint OK (b2 == b3)" \
		|| { echo "FAIL: seed-built compiler does not converge"; exit 1; }
	@cmp -s $(BUILDDIR)/b1.c bootstrap/zc.c \
		&& echo "seed is current (b1 == committed seed)" \
		|| echo "note: seed has lagged (b1 != committed seed) -- run 'make bump-seed' when convenient"
	$(BUILDDIR)/zc-b1 ztypes --src src --system lib/system --emit-c $(BUILDDIR)/zt.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zt $(BUILDDIR)/zt.c -lquadmath -lm
	$(BUILDDIR)/zt | diff - tests/fixtures/ztypes_z/smoke.expected \
		&& echo "correctness OK (seed-built zc compiles ztypes to golden)"
	@echo "bootstrap seed OK: 'cc bootstrap/zc.c' builds a correct self-hosting zc (no Python)"

# install -- a self-contained tree at $(ROOT) + a $(BINDIR)/zc symlink.
install: bin/zc bin/zl bin/zls
	mkdir -p $(ROOT)/bin $(ROOT)/lib $(BINDIR)
	cp bin/zc $(ROOT)/bin/zc
	cp bin/zl $(ROOT)/bin/zl
	cp bin/zls $(ROOT)/bin/zls
	rm -rf $(ROOT)/lib/system $(ROOT)/lib/runtime $(ROOT)/docs $(ROOT)/src
	cp -r lib/system $(ROOT)/lib/system
	cp -r src/runtime $(ROOT)/lib/runtime
	cp -r docs $(ROOT)/docs
	cp -r src $(ROOT)/src
	ln -sf $(ROOT)/bin/zc $(BINDIR)/zc
	ln -sf $(ROOT)/bin/zl $(BINDIR)/zl
	ln -sf $(ROOT)/bin/zls $(BINDIR)/zls
	@echo "installed zc, zl, zls -> $(BINDIR) (tree: $(ROOT))"

# docs -- render the .pdoc documentation to HTML. Commit the regenerated .html.
# Needs the picodoc renderer at ../picodoc-c/picodoc (see docs/Makefile).
docs:
	$(MAKE) -C docs
	@echo "rendered docs/ -- commit the regenerated .html"

# warn-check -- compile the emitted compiler C with every warning as an error.
warn-check: bin/zc
	$(CC) $(CFLAGS) $(OPTFLAGS) -Werror -c bin/zc.c -o /dev/null
	@echo "warn-check OK: zero compiler warnings"

# perf -- self-compile performance snapshot for docs/perf-baseline.md, measured the
# same way as the rows there (default hash, --emit-c /dev/null): the zerolang line
# count (compiler + relocated front-end/stdlib), self-compile wall best-of-5 + peak
# RSS, the parse/typecheck/emit phase split, and -- when valgrind is installed -- the
# ground-truth allocation total (heap blocks for one self-compile). Append the printed
# numbers as a row to docs/perf-baseline.md in the commit that lands a perf-relevant
# change. The glibc wall (make MIMALLOC=0), corpus wall (make test) and the DHAT
# allocation-site census stay manual -- see the command list in that doc.
PERFRUN := bin/zc zc --src src --system lib/system --emit-c /dev/null
perf: bin/zc
	@echo "== zerolang line count (.z) =="
	@lsrc=$$(cat src/*.z | wc -l); llib=$$(cat lib/system/*.z | wc -l); \
	  printf "  src/*.z: %s    lib/system/*.z: %s    total: %s\n" "$$lsrc" "$$llib" "$$((lsrc + llib))"
	@echo "== self-compile wall best-of-5 (mimalloc; drop run 1) + peak RSS =="
	@for i in 1 2 3 4 5; do /usr/bin/time -f "  %es  %MkB" $(PERFRUN) 2>&1 | tail -1; done
	@echo "== phase split (parse / typecheck / emit) =="
	@bin/zc zc --src src --system lib/system --time --emit-c /dev/null 2>&1 | tail -1 | sed 's/^/  /'
	@echo "== allocations (valgrind memcheck: total heap blocks for one self-compile) =="
	@if command -v valgrind >/dev/null 2>&1; then \
	  valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: /  /'; \
	else echo "  (valgrind not installed -- skipping alloc total)"; fi

# shadow-guard -- ratchet against the user-shadow miscompile class. The C emitter
# must derive a type's C type from its canonical type id (typeRefC / scalarCTypeFor
# / cTypeForNameTid), never from the type NAME (cTypeOf / cTypeForName) directly --
# otherwise a user type shadowing a builtin scalar (i64: record {...}) emits the C
# scalar instead of its struct. The baselines pin the known-safe remaining by-name
# sites (numeric casts, userFnId-first dispatch, control-flow checks, and the
# head-gated assignment / fnSignature / typeRefC sites); a new by-name site grows
# the count and fails. New type emission must go through the id-based helpers.
# any-guard -- `Any` is the bound that says the family genuinely does not
# matter. After the val/ref split no USER source may say it: a generic names
# AnyVal or AnyRef. Two stdlib files keep counted residuals:
#   system.z (7) -- `return` and `typedef`, whose parameter is never consulted
#     (probed: bounding them to AnyVal does not reject a reftype), plus
#     `Iterator` and `Result` and `OptionView`, which still span both families.
#   collections.z (7) -- the containers, which the P5 split replaces with
#     per-family templates. This baseline goes to 0 there.
# Both are ratchets: they may only DECREASE. Enforced here rather than in the
# typechecker because generic-param registration has no unit name in hand.
any-guard:
	@u=$$(grep -rn 'Any\.generic' --include=*.z src examples tests | grep -vE ':[0-9]+: *#' | wc -l); \
	if [ "$$u" -gt 0 ]; then \
	  echo "any-guard FAIL: $$u use(s) of Any.generic in user source"; \
	  grep -rn 'Any\.generic' --include=*.z src examples tests | grep -vE ':[0-9]+: *#'; \
	  echo "  A generic must name the family it takes: AnyVal.generic or AnyRef.generic."; \
	  exit 1; \
	fi; \
	s=$$(grep -c 'Any\.generic' lib/system/system.z); \
	c=$$(grep -c 'Any\.generic' lib/system/collections.z); \
	fail=0; \
	if [ "$$s" -gt 7 ]; then echo "any-guard FAIL: system.z Any.generic = $$s (baseline 7)"; fail=1; fi; \
	if [ "$$c" -gt 7 ]; then echo "any-guard FAIL: collections.z Any.generic = $$c (baseline 7)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then echo "  Lower the baseline here when a residual is legitimately removed."; exit 1; fi; \
	echo "any-guard OK: user source clean; system.z=$$s (<=7) collections.z=$$c (<=7)"

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
# The last two pin where C names are BUILT: the type checker composes none, and
# the emitter spells the z_t{id} shape only inside its one composer, which the
# per-program table in emitC calls once per type.
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
	g1=$$(grep -c 'composeCname' src/ztypes.z); \
	g2=$$(grep -cF 'z_t\{' src/zemitterc.z); \
	fail=0; \
	if [ "$$g1" -gt 0 ]; then echo "emitter-guard FAIL: composeCname in src/ztypes.z = $$g1 (baseline 0)"; fail=1; fi; \
	if [ "$$g2" -gt 3 ]; then echo "emitter-guard FAIL: 'z_t{' literals in src/zemitterc.z = $$g2 (baseline 3)"; fail=1; fi; \
	if [ "$$e1" -gt 23 ]; then echo "emitter-guard FAIL: ztypecheck.resolvedByKey = $$e1 (baseline 23)"; fail=1; fi; \
	if [ "$$e2" -gt 5 ]; then echo "emitter-guard FAIL: ztypecheck.walkLookupTyperef = $$e2 (baseline 5)"; fail=1; fi; \
	if [ "$$e3" -gt 22 ]; then echo "emitter-guard FAIL: resolveTypeIdByName = $$e3 (baseline 22)"; fail=1; fi; \
	if [ "$$e4" -gt 35 ]; then echo "emitter-guard FAIL: userFnId = $$e4 (baseline 35)"; fail=1; fi; \
	if [ "$$e5" -gt 0 ]; then echo "emitter-guard FAIL: childOwnershipText = $$e5 (baseline 0)"; fail=1; fi; \
	if [ "$$e6" -gt 94 ]; then echo "emitter-guard FAIL: typeNameOfReg9 = $$e6 (baseline 94)"; fail=1; fi; \
	if [ "$$e7" -gt 2 ]; then echo "emitter-guard FAIL: ztypes.mangleVarName = $$e7 (baseline 2, both inside varCName: the --readable-names spelling and the no-id fallback)"; fail=1; fi; \
	if [ "$$e8" -gt 5 ]; then echo "emitter-guard FAIL: io.readText = $$e8 (baseline 5)"; fail=1; fi; \
	if [ "$$e9" -gt 8 ]; then echo "emitter-guard FAIL: monoOriginName = $$e9 (baseline 8)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  ctxCname instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: resolvedByKey=$$e1 walkLookup=$$e2 resolveByName=$$e3 userFnId=$$e4 ownText=$$e5 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 monoOrigin=$$e9 (monoOrigin baseline 8)"

# member-guard -- ratchet against declaration-bypassing member special-cases in
# the type checker. The single-source-of-truth arc removed the hardcoded member
# shortcuts (the .string-on-String reject guard and the moMiss9 String->
# StringView retry that let a String silently inherit StringView's read surface).
# What remains are the sanctioned string-keyed markers: ownership (lock / borrow /
# take / hold / view / release), definition keywords (typedef / private / public / return /
# error / panic / create / copy / tag / array / index), and the .stringview / .str
# conversions. A rising count means a new hardcoded string-keyed member/marker
# special-case -- resolve members through their declared childOf edges instead
# (the system units are the source of truth). Bump the baseline here only for a
# genuinely-sanctioned marker.
member-guard:
	@m1=$$(grep -c 'cn.stringview ==' src/ztypecheck.z); \
	if [ "$$m1" -gt 37 ]; then \
	  echo "member-guard FAIL: 'cn.stringview ==' = $$m1 (baseline 37)"; \
	  echo "  A new hardcoded string-keyed member/marker special-case was added to the"; \
	  echo "  type checker. Resolve members through their declared childOf edges (the"; \
	  echo "  system units are the source of truth); bump the baseline only for a"; \
	  echo "  genuinely-sanctioned marker."; \
	  exit 1; \
	fi; \
	echo "member-guard OK: cn.stringview == = $$m1 (<=37)"

# view-guard -- a native receiver marked `.view` asserts that the C never writes
# through it, and the compiler cannot check that: there is no body. So the C
# compiler proves it instead -- the receiver is declared `const`, and (with
# -Werror=discarded-qualifiers) any write, direct or through a helper or a
# vtable, fails the build. This guard keeps the two halves from drifting: a
# `.view` declaration must have a const receiver in its backing, and a const
# receiver must be declared `.view` so the marker does not go unused.
#
# Backings come in four shapes: one fragment per method under
# src/runtime/natives, many functions in one file (z_String.inc), the @@NAME@@
# container templates, and C the emitter builds from string literals (List.get,
# .contains, .listview, .sort, .iterate, ListIter.call, Map.getv). So a receiver
# is found by TYPE rather than by parameter name -- the first parameter whose
# struct is the function's own prefix, which covers self / _this / _it / _e /
# s / a alike. VIEW_GUARD_PLACEHOLDER names the type each template placeholder
# stands for; an unmapped placeholder names a user type or a valtype (array,
# str, the protocol vtable, meta.create), and `.view` does not apply to either.
# An emitter-built function names a runtime mono rather than a type the guard
# can read, so VIEW_GUARD_EMITTED says which declaration each one backs; `-`
# marks the ones backing no reference-type method at all (array / str value
# equality, a union destructor, List.extendView).
#
# A receiver-bearing C function that resolves to no declaration is an ERROR,
# not a skip -- a silently unchecked backing is exactly what the guard exists
# to prevent. One C function backing several declarations is spelt out in
# VIEW_GUARD_BACKS; a helper with no zerolang declaration at all goes in
# VIEW_GUARD_INTERNAL.
#
# The other direction closes the same hole from the declaration end: a native
# receiver that reaches no C function the guard can read would otherwise carry
# an unchecked marker, so every one of them is registered in VIEW_GUARD_INLINE
# with the reason it cannot be const-checked --
#   inline      the receiver is read in place (a field read or a compound
#               literal); no C function receives it
#   byvalue     the C receives a copy, so it cannot write through to the source
#   unemitted   declared, but nothing emits a call to it
#   Type.method the receiver is projected and handed to another declared
#               method, which must itself be `.view` before this one may be
# A registered entry that turns out to HAVE a backing is an error too, so the
# register cannot go stale. String's comparisons are the reason the last kind
# exists: `s1 == s2` does not call z_String_eq (which nothing calls) -- it
# converts both sides to by-value views and calls z_StringView_eq.

VIEW_GUARD_PLACEHOLDER := z_List.c.tmpl=@@NAME@@:List z_Map.c.tmpl=@@NAME@@:Map \
  z_MapIter.c.tmpl=@@NAME@@:Map,@@MAPKEYITER@@:MapKeyIter,@@MAPITEMITER@@:MapItemIter,@@MAPENTRY@@:MapEntry \
  z_Set.c.tmpl=@@NAME@@:Set,@@SETITER@@:SetIter
VIEW_GUARD_EMITTED := get:List.get,ListView.get getMut:List.getMut,ListView.getMut \
  contains:List.contains \
  listview:List.listview sort:List.sort iterate:List.iterate \
  call:ListIter.call,ListIterVal.call \
  iterateMut:List.iterateMut getv:Map.getv eq:- extendView:- destroy:-
VIEW_GUARD_BACKS := StringView.eq===,!= StringView.cmp=compare,<,<=,>,>=
VIEW_GUARD_INTERNAL := String.cat String.print String.free String.eq String.cmp \
  StringView.print StringView.indexOfRaw StringView.replaceImpl \
  List.destroy List.grow Map.destroy Map.grow Map.find \
  Set.destroy Set.grow Set.find MapEntry.key MapEntry.value
VIEW_GUARD_INLINE := Bytes.byteview:unemitted \
  ListVal.append:List.append ListVal.insert:List.insert \
  ListVal.extend:List.extend ListVal.get:List.get ListVal.set:List.set \
  ListVal.pop:List.pop ListVal.contains:List.contains \
  ListVal.getMut:List.getMut \
  ListVal.sort:List.sort ListVal.listview:List.listview \
  ListVal.iterate:List.iterate ListVal.iterateMut:List.iterateMut \
  ListVal.length:inline ListVal.capacity:inline \
  SetVal.add:Set.add SetVal.has:Set.has SetVal.delete:Set.delete \
  SetVal.iterate:Set.iterate SetIterVal.call:SetIter.call \
  SetVal.length:inline SetVal.capacity:inline \
  MapRV.get:Map.get MapRV.getv:Map.getv MapRV.set:Map.set MapRV.has:Map.has \
  MapRV.remove:Map.remove MapRV.iterate:Map.iterate \
  MapRV.iterateItems:Map.iterateItems \
  MapRV.length:inline MapRV.capacity:inline \
  MapVR.get:Map.get MapVR.set:Map.set MapVR.has:Map.has \
  MapVR.remove:Map.remove MapVR.iterate:Map.iterate \
  MapVR.iterateItems:Map.iterateItems \
  MapVR.length:inline MapVR.capacity:inline \
  MapVV.get:Map.get MapVV.set:Map.set MapVV.has:Map.has \
  MapVV.remove:Map.remove MapVV.iterate:Map.iterate \
  MapVV.iterateItems:Map.iterateItems \
  MapVV.length:inline MapVV.capacity:inline \
  List.length:inline List.capacity:inline ListView.length:inline \
  Map.length:inline Map.capacity:inline Set.length:inline Set.capacity:inline \
  String.length:inline String.capacity:inline String.stringview:inline \
  StringView.length:inline StringView.string:byvalue \
  String.contains:StringView.contains String.startsWith:StringView.startsWith \
  String.endsWith:StringView.endsWith String.count:StringView.count \
  String.hash:StringView.hash String.substring:StringView.substring \
  String.==:StringView.== String.!=:StringView.!= String.<:StringView.< \
  String.<=:StringView.<= String.>:StringView.> String.>=:StringView.>= \
  String.compare:StringView.compare

define VIEW_GUARD_AWK
# Reads lib/system/*.z (declarations) and the C backings, then joins them.

function camel(s,   out, i, c, up) {
    out = ""; up = 0
    for (i = 1; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "_") { up = 1; continue }
        out = out (up ? toupper(c) : c); up = 0
    }
    return out
}

# A native method declaring a receiver, on a reference type: record whether
# the receiver carries the .view marker.
function declEmit() {
    # `takex` is a RETURN marker, not a receiver: without the trailing boundary
    # `this.takex` prefix-matches `this.take` and the guard hunts for a receiver
    # the declaration never had.
    if ((dacc !~ /[{ ]:this[ }]/) && (dacc !~ /this\.(view|lock|borrow|take)[^a-z]/)) return
    dm = (dacc ~ /this\.view/) ? "view" : "plain"
    if (!((dty " " dmeth) in dseen)) { dord[++dn] = dty " " dmeth; dseen[dty " " dmeth] = 1 }
    dkind[dty " " dmeth] = dm
}

# One (type, method) -> receiver class. A prototype and its definition must
# agree, so a disagreement is itself a finding.
function crow(t, m, k, f,   key) {
    key = t " " m
    if (key in ckind && ckind[key] != k) {
        print "view-guard FAIL: " t "." m " is backed by both a " ckind[key] " and a " k " receiver (" cfn[key] ") -- a prototype and its definition disagree"
        bad = 1
        return
    }
    if (!(key in ckind)) cord[++cn] = key
    ckind[key] = k
    cfn[key] = f
}

# A C signature whose FIRST parameter is its own struct declares a receiver.
# Keying on the type rather than the parameter name covers self / _this / _it /
# _e / s / a alike, and reads the method straight off the C name.
function scanSig(sig, emitted,   fname, rest, ce, cc, p1, pty, cty, pfx, cm, ck, i, na, al, e, hit, nm, ms, j) {
    if (match(sig, /z_[A-Za-z0-9_@]+[ \t]*\(/) == 0) return
    fname = substr(sig, RSTART, RLENGTH)
    rest = substr(sig, RSTART + RLENGTH)
    sub(/[ \t]*\($$/, "", fname)

    ce = index(rest, ",")
    cc = index(rest, ")")
    if (ce == 0 || (cc > 0 && cc < ce)) ce = cc
    if (ce == 0) return
    p1 = substr(rest, 1, ce - 1)

    if (match(p1, /z_[A-Za-z0-9_@]+_t/) == 0) return
    pty = substr(p1, RSTART, RLENGTH)
    cty = substr(pty, 3, length(pty) - 4)
    pfx = "z_" cty "_"
    if (substr(fname, 1, length(pfx)) != pfx) return
    cm = camel(substr(fname, length(pfx) + 1))
    if (cm == "") return
    ck = (p1 ~ /\*/) ? ((p1 ~ /const/) ? "const" : "ptr") : "byvalue"

    # The emitter builds its C from string literals, so the type is a runtime
    # mono name rather than a spelling the guard can read: VIEW_GUARD_EMITTED
    # says which declaration each of those functions backs.
    if (emitted) {
        na = split(EMITTED, al, " ")
        for (i = 1; i <= na; i++) {
            e = index(al[i], ":")
            if (substr(al[i], 1, e - 1) != cm) continue
            if (substr(al[i], e + 1) == "-") return
            nm = split(substr(al[i], e + 1), ms, ",")
            for (j = 1; j <= nm; j++) {
                sub(/\./, " ", ms[j])
                split(ms[j], mt, " ")
                crow(mt[1], mt[2], ck, "the emitter's z_..._" cm)
            }
            return
        }
        print "view-guard FAIL: the emitter builds a z_..._" cm " with a receiver but VIEW_GUARD_EMITTED does not say which declaration it backs"
        bad = 1
        return
    }

    # A placeholder with no mapping names a user type or a valtype (array, str,
    # the protocol vtable, meta.create): .view does not apply there.
    if (cty in ph) cty = ph[cty]
    else if (cty ~ /@@/) return

    # One C function can back several declarations (z_String_cmp is compare and
    # the four orderings); expand it into one row per method it backs.
    na = split(ALIAS, al, " ")
    hit = 0
    for (i = 1; i <= na; i++) {
        e = index(al[i], "=")
        if (substr(al[i], 1, e - 1) != cty "." cm) continue
        hit = 1
        nm = split(substr(al[i], e + 1), ms, ",")
        for (j = 1; j <= nm; j++) crow(cty, ms[j], ck, fname)
    }
    if (!hit) crow(cty, cm, ck, fname)
}

FNR == 1 {
    isemit = (FILENAME ~ /zemitterc\.z$$/)
    isdecl = (FILENAME ~ /\.z$$/) && !isemit
    if (isdecl) { dty = ""; grab = 0 }
    else if (!isemit) {
        base = FILENAME; sub(/.*\//, "", base)
        delete ph
        np = split(PH, pf, " ")
        for (i = 1; i <= np; i++) {
            e = index(pf[i], "=")
            if (substr(pf[i], 1, e - 1) != base) continue
            nk = split(substr(pf[i], e + 1), kv, ",")
            for (j = 1; j <= nk; j++) {
                p = index(kv[j], ":")
                ph[substr(kv[j], 1, p - 1)] = substr(kv[j], p + 1)
            }
        }
        buf = ""; cap = 0
    }
}

# ---------------- declaration side ----------------

isdecl {
    if ($$0 ~ /^[A-Za-z][A-Za-z0-9]*: (class|union|protocol)/) {
        dty = $$1; sub(/:.*/, "", dty); grab = 0; next
    }
    if ($$0 ~ /^[A-Za-z][A-Za-z0-9]*: (record|variant|facet)/) { dty = ""; grab = 0; next }
    if (dty == "") next
    if (grab) {
        dacc = dacc " " $$0
        dlines++
        if (dacc ~ /is native/) { grab = 0; declEmit() }
        else if (dacc ~ /\bis \{/ || dlines > 14) grab = 0
        next
    }
    if ($$0 ~ /^[ \t]+([A-Za-z_][A-Za-z0-9_]*|[=!<>+*\/%-]+): function/) {
        dmeth = $$1; sub(/:$$/, "", dmeth)
        dacc = $$0; dlines = 1
        if (dacc ~ /is native/) { declEmit(); next }
        if (dacc ~ /\bis \{/) next
        grab = 1
    }
    next
}

# ---------------- C the emitter builds ----------------
# A `"static ...` literal, with every \{interpolation} folded to a placeholder.

isemit {
    if (index($$0, "\"static ") == 0) next
    lit = substr($$0, index($$0, "\"static ") + 1)
    gsub(/\\[{][^}]*[}]/, "@@", lit)
    scanSig(lit, 1)
    next
}

# ---------------- C backing files ----------------

{
    if (cap) buf = buf " " $$0
    else if ($$0 ~ /^(static|ZINLINE)[ \t].*z_[A-Za-z0-9_@]+[ \t]*\(/) { buf = $$0; cap = 1 }
    else next
    if (index(buf, ")") == 0) next
    cap = 0
    scanSig(buf, 0)
}

END {
    ni = split(INTERNAL, iv, " ")
    for (i = 1; i <= ni; i++) internal[iv[i]] = 1

    for (i = 1; i <= cn; i++) {
        key = cord[i]
        t = key; sub(/ .*/, "", t)
        m = key; sub(/^[^ ]* /, "", m)
        if (!(key in dkind)) {
            if ((t "." m) in internal) { nint++; continue }
            print "view-guard FAIL: " cfn[key] " carries a " t " receiver but resolves to no " t "." m " declaration -- back it with a declaration, or list " t "." m " in VIEW_GUARD_INTERNAL"
            bad = 1
            continue
        }
        if (ckind[key] == "byvalue") { nval++; continue }
        isc = (ckind[key] == "const")
        isv = (dkind[key] == "view")
        checked++
        if (isc && isv) { nview++; continue }
        if (isc && !isv) {
            print "view-guard FAIL: " cfn[key] " has a const receiver but " t "." m " is not declared '.view' -- the marker is available and unused"
            bad = 1
        } else if (!isc && isv) {
            print "view-guard FAIL: " t "." m " is declared '.view' but " cfn[key] " has a NON-const receiver -- the C may write through it"
            bad = 1
        }
    }
    # Every declared receiver must be accounted for: const-checked above, a
    # by-value copy the C cannot write through, or an entry in the register.
    nr = split(INLINE, rv, " ")
    for (i = 1; i <= nr; i++) {
        e = index(rv[i], ":")
        rwhy[substr(rv[i], 1, e - 1)] = substr(rv[i], e + 1)
    }
    for (i = 1; i <= dn; i++) {
        key = dord[i]
        t = key; sub(/ .*/, "", t)
        m = key; sub(/^[^ ]* /, "", m)
        dot = t "." m
        if (key in ckind) {
            if (dot in rwhy) {
                print "view-guard FAIL: " dot " is in VIEW_GUARD_INLINE but " cfn[key] " does receive it -- drop the entry, the const check covers it"
                bad = 1
            }
            continue
        }
        if (!(dot in rwhy)) {
            print "view-guard FAIL: " dot " declares a receiver that reaches no C function the guard can read -- say why in VIEW_GUARD_INLINE (inline / byvalue / unemitted / <Type.method> it delegates to)"
            bad = 1
            continue
        }
        nreg++
        why = rwhy[dot]
        if (why == "inline" || why == "byvalue" || why == "unemitted") continue
        tgt = why; sub(/\./, " ", tgt)
        if (!(tgt in dkind)) {
            print "view-guard FAIL: " dot " is registered as delegating to " why ", which is not a declared native receiver"
            bad = 1
        } else if (dkind[key] == "view" && dkind[tgt] != "view") {
            print "view-guard FAIL: " dot " is declared '.view' but delegates to " why ", which is not -- the reader it defers to must be one first"
            bad = 1
        }
    }

    if (bad) exit 1
    printf "view-guard OK: %d native receivers const-checked in C (%d '.view'), %d by-value, %d registered, %d internal\n", checked, nview, nval, nreg, nint
}
endef
export VIEW_GUARD_AWK

view-guard:
	@awk -v PH='$(VIEW_GUARD_PLACEHOLDER)' -v ALIAS='$(VIEW_GUARD_BACKS)' \
	  -v INTERNAL='$(VIEW_GUARD_INTERNAL)' -v EMITTED='$(VIEW_GUARD_EMITTED)' \
	  -v INLINE='$(VIEW_GUARD_INLINE)' \
	  "$$VIEW_GUARD_AWK" lib/system/*.z src/zemitterc.z \
	  src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl

# fallback-guard -- the emitter must never silently degrade: a construct it
# cannot emit leaves a "/* zemitterc: unhandled ... */" marker in the C (and
# records an emitFail, so zc exits nonzero). Leg 1: no example emit outside
# the known baseline may carry a marker (the baseline holds the known gaps
# and shrinks to empty as they are fixed). Leg 2: the emitted driver C
# (bin/zc.c, out/zl.c, out/zls.c) must carry ZERO live markers. The drivers
# compile the emitter, so its own message strings appear there as literals;
# those are excluded by content -- a marker opening a C string is the
# emitter quoting itself, whereas a live one is emitted bare, either as its
# own comment line or inline as `= /* ... */0`. Matching on the literal (not
# on a helper's C name) keeps the leg working whichever naming scheme
# --readable-names selects. Leg 3: a source ratchet on the
# emitFail line count in src/zemitterc.z -- it may only DECREASE as fallback
# legs are resolved; lower the baseline in the same commit that removes a leg.
FALLBACK_BASELINE :=
EMITFAIL_BASELINE := 22
EXCS := $(NAMES:%=$(EXDIR)/%.c)
fallback-guard: $(EXCS) bin/zc bin/zl bin/zls
	@fail=0; \
	for f in $(EXCS); do \
	  if grep -q 'zemitterc: unhandled' $$f; then \
	    name=$$(basename $$f .c); \
	    case " $(FALLBACK_BASELINE) " in \
	      *" $$name "*) ;; \
	      *) echo "fallback-guard FAIL: $$name.c carries an unhandled-construct marker"; fail=1;; \
	    esac; \
	  fi; \
	done; \
	for d in bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c; do \
	  n=$$(grep 'zemitterc: unhandled' $$d | grep -cv '"[[:space:]]*/\* zemitterc: unhandled'); \
	  if [ "$$n" -gt 0 ]; then \
	    echo "fallback-guard FAIL: $$d carries $$n live unhandled-construct marker(s)"; fail=1; \
	  fi; \
	done; \
	n=$$(grep -c 'emitFail' src/zemitterc.z); \
	if [ "$$n" -gt $(EMITFAIL_BASELINE) ]; then \
	  echo "fallback-guard FAIL: src/zemitterc.z emitFail lines = $$n (baseline $(EMITFAIL_BASELINE)) -- new fallback leg?"; fail=1; \
	elif [ "$$n" -lt $(EMITFAIL_BASELINE) ]; then \
	  echo "fallback-guard: emitFail lines = $$n < baseline $(EMITFAIL_BASELINE) -- lower EMITFAIL_BASELINE"; \
	fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  The emitter hit a construct it cannot emit. Fix the emission gap (or,"; \
	  echo "  for a known example gap being tracked, add it to FALLBACK_BASELINE)."; \
	  exit 1; \
	fi; \
	echo "fallback-guard OK: no unhandled-construct markers outside the baseline ($(words $(FALLBACK_BASELINE)) known; emitFail legs $$n)"

clean:
	rm -rf $(BUILDDIR) bin

# native-guard -- the io/os/cli/net natives are declaration-driven: the
# unified emitter derives the C symbol z_<unit>_<name> from the resolved
# declaration, and the C implementation lives in a conventionally-named
# fragment _Z_<UNIT>_<UPPER_SNAKE(name)>.inc under src/runtime/natives.
# Leg 1: every top-level 'is native' free function in the four convention
# units has its fragment on disk, or is a known exception (print is the
# statement-special; stdin/stdout/stderr live in the stream fragments;
# env->GET_ENV and pollReadable->POLL are renamed). Bodied free functions
# emit generically and are exempt. Leg 2: every _Z_* fragment name the
# emitter references exists on disk.
NATIVE_GUARD_EXCEPTIONS := io.print io.stdin io.stdout io.stderr os.env net.pollReadable
native-guard:
	@fail=0; \
	for u in io os cli net; do \
	  for n in $$(awk '/^[a-zA-Z][a-zA-Z0-9]*: function/ {name=$$1; sub(/:.*/,"",name); pending=1} pending && /is native/ {print name; pending=0} pending && /is \{/ {pending=0}' lib/system/$$u.z); do \
	    case " $(NATIVE_GUARD_EXCEPTIONS) " in *" $$u.$$n "*) continue;; esac; \
	    snake=$$(echo "$$n" | sed 's/\([A-Z]\)/_\1/g' | tr 'a-z' 'A-Z'); \
	    frag="_Z_$$(echo $$u | tr 'a-z' 'A-Z')_$$snake"; \
	    test -f src/runtime/natives/$$frag.inc || { echo "native-guard: $$u.$$n declared native but $$frag.inc missing (add the fragment or an exceptions entry)"; fail=1; }; \
	  done; \
	done; \
	for f in $$(grep -oE '"_Z_[A-Z0-9_]+"' src/zemitterc.z | tr -d '"' | sort -u); do \
	  test -f src/runtime/natives/$$f.inc || { echo "native-guard: fragment $$f.inc referenced but missing"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "native-guard OK: native declarations and runtime fragments consistent"
