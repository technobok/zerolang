CC       := gcc
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types

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

.PHONY: all check test ci ci-corpus build clean style-lint style-lint-fast zc zl zls install regen-goldens bump-seed test-bootstrap docs warn-check perf shadow-guard emitter-guard native-guard fallback-guard member-guard

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
ci: style-lint shadow-guard emitter-guard native-guard fallback-guard member-guard ci-corpus
	$(MAKE) --no-print-directory test-bootstrap
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

ci-corpus: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

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
	if [ "$$e3" -gt 39 ]; then echo "emitter-guard FAIL: resolveTypeIdByName = $$e3 (baseline 39)"; fail=1; fi; \
	if [ "$$e4" -gt 41 ]; then echo "emitter-guard FAIL: userFnId = $$e4 (baseline 41)"; fail=1; fi; \
	if [ "$$e5" -gt 0 ]; then echo "emitter-guard FAIL: childOwnershipText = $$e5 (baseline 0)"; fail=1; fi; \
	if [ "$$e6" -gt 121 ]; then echo "emitter-guard FAIL: typeNameOfReg9 = $$e6 (baseline 121)"; fail=1; fi; \
	if [ "$$e7" -gt 20 ]; then echo "emitter-guard FAIL: ztypes.mangleVarName = $$e7 (baseline 20)"; fail=1; fi; \
	if [ "$$e8" -gt 5 ]; then echo "emitter-guard FAIL: io.readText = $$e8 (baseline 5)"; fail=1; fi; \
	if [ "$$e9" -gt 37 ]; then echo "emitter-guard FAIL: monoOriginName = $$e9 (baseline 37)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  reg.cnameOf instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: resolvedByKey=$$e1 walkLookup=$$e2 resolveByName=$$e3 userFnId=$$e4 ownText=$$e5 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 monoOrigin=$$e9 (monoOrigin baseline 37)"

# member-guard -- ratchet against declaration-bypassing member special-cases in
# the type checker. The single-source-of-truth arc removed the hardcoded member
# shortcuts (the .string-on-String reject guard and the moMiss9 String->
# StringView retry that let a String silently inherit StringView's read surface).
# What remains are the sanctioned string-keyed markers: ownership (lock / borrow /
# take / release), definition keywords (typedef / private / public / return /
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

# fallback-guard -- the emitter must never silently degrade: a construct it
# cannot emit leaves a "/* zemitterc: unhandled ... */" marker in the C (and
# records an emitFail, so zc exits nonzero). Leg 1: no example emit outside
# the known baseline may carry a marker (the baseline holds the known gaps
# and shrinks to empty as they are fixed). Leg 2: the emitted driver C
# (bin/zc.c, out/zl.c, out/zls.c) must carry ZERO live markers -- lines
# holding the emitter's own message-string literals (String_append / _zs
# constants) are excluded from the count. Leg 3: a source ratchet on the
# emitFail line count in src/zemitterc.z -- it may only DECREASE as fallback
# legs are resolved; lower the baseline in the same commit that removes a leg.
FALLBACK_BASELINE :=
EMITFAIL_BASELINE := 21
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
	  n=$$(grep 'zemitterc: unhandled' $$d | grep -v 'String_append' | grep -cv '_zs'); \
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
