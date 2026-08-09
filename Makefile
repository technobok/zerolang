CC       := gcc
# -Wdiscarded-qualifiers is the GCC spelling; clang groups the same
# diagnostic under -Wincompatible-pointer-types-discards-qualifiers.
QUALWERR := $(if $(findstring clang,$(shell $(CC) --version 2>/dev/null | head -1)),-Werror=incompatible-pointer-types-discards-qualifiers,-Werror=discarded-qualifiers)
CFLAGS   := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types \
            $(QUALWERR)

# Parallel by default: make fans out independent targets and the corpus runner
# fans out its per-case pipelines (--jobs). `make NPROC=1` forces everything
# serial (NPROC feeds both -j and the runner's --jobs).
NPROC    ?= $(shell nproc 2>/dev/null || echo 1)
MAKEFLAGS += -j$(NPROC)
# Daily-driver binaries only (bin/zc, bin/zl, bin/zls -- the three `make
# install` lays down). -fwrapv and -fno-strict-aliasing pin down the C the
# emitter relies on. Bootstrap intermediates and the test runner stay -O0:
# they are built once and run once, so gcc time dominates.
# -O2 is the RELEASE level: self-compile wall 0.53s -> 0.37s, for a bin/zc.c
# compile of 14s -> 20s. Set OPTFLAGS='-O1 -fno-strict-aliasing -fwrapv' for
# a faster edit-test loop.
OPTFLAGS := -O2 -fno-strict-aliasing -fwrapv
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

# The perf series is -O1 and stays -O1, on a binary of its own. Every row in
# docs/perf-baseline.md was measured that way; -O2 moves wall but not one
# allocation, so mixing levels would make the wall column meaningless while
# leaving the allocation column looking fine. A dedicated binary also means a
# driver rebuild -- at another level, or by another compiler -- can never leak
# into a measurement, which is the trap perf-strict used to guard against by
# inspecting bin/zc. See "Toolchain findings" in docs/perf-baseline.md.
PERFOPT  := -O1 -fno-strict-aliasing -fwrapv
PERFCC   ?= gcc
PERFBIN  := $(BUILDDIR)/zc-perf

# The build stamp the three drivers link in, so `zc --version` can name the
# commit it was built from. Empty when git is unavailable (a release tarball,
# an exported tree), and the version line then simply carries no build
# metadata. It arrives as a LINKED SYMBOL, overriding the weak default in
# src/runtime/natives/_Z_OS_BUILD_COMMIT.inc, rather than as a -D: the natives
# that read it live inside the drivers' single multi-megabyte translation
# unit, so a define costs a 20s recompile per commit where a link costs 41ms.
BUILDID   := $(shell git rev-parse --short=8 HEAD 2>/dev/null)
BUILDID   := $(if $(BUILDID),$(BUILDID)$(shell git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || echo .dirty))
BUILDDATE := $(if $(BUILDID),$(shell git show -s --format=%cs HEAD 2>/dev/null))

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

.PHONY: all check test ci ci-corpus build clean style-lint style-lint-fast zc zl zls install regen-goldens bump-seed test-bootstrap docs warn-check perf shadow-guard emitter-guard native-guard fallback-guard member-guard deadcode-guard readable-check perf-strict perf-elision

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
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c -lm

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
ci: style-lint shadow-guard emitter-guard native-guard view-guard fallback-guard member-guard any-guard deadcode-guard eager-guard case-guard readable-check ci-corpus
	$(MAKE) --no-print-directory test-bootstrap
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

ci-corpus: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

# readable-check -- --readable-names is a debug affordance, so nothing else
# exercises it; without this it would rot unnoticed. The two schemes differ
# only in identifier spelling, so the built programs must behave identically.
readable-check: bin/zc
	@mkdir -p $(BUILDDIR)/rn
	@for n in hello vector records fibonacci typedefs; do \
	  bin/zc $$n --src examples --system lib/system --emit-c $(BUILDDIR)/rn/$$n-id.c || exit 1; \
	  bin/zc $$n --src examples --system lib/system --readable-names --emit-c $(BUILDDIR)/rn/$$n-rn.c || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-id $(BUILDDIR)/rn/$$n-id.c -lm || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-rn $(BUILDDIR)/rn/$$n-rn.c -lm || exit 1; \
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
# out/buildstamp.c -- regenerated every run but REWRITTEN only when its text
# changes, so its timestamp (and the relink it triggers) moves only when the
# commit or the tree's cleanliness actually moved. FORCE is what makes the
# recipe run; cmp is what makes the write conditional.
.PHONY: FORCE
FORCE:

$(BUILDDIR)/buildstamp.c: FORCE
	@mkdir -p $(BUILDDIR)
	@printf 'const char z_build_commit[] = "%s";\nconst char z_build_date[] = "%s";\n' \
	  '$(BUILDID)' '$(BUILDDATE)' > $@.tmp
	@cmp -s $@.tmp $@ || mv -f $@.tmp $@
	@rm -f $@.tmp

$(BUILDDIR)/buildstamp.o: $(BUILDDIR)/buildstamp.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c $< -o $@

$(BUILDDIR)/mimalloc.o: vendor/mimalloc/src/static.c vendor/mimalloc/zc_tune.c $(wildcard vendor/mimalloc/src/*.c) $(wildcard vendor/mimalloc/include/*.h)
	@mkdir -p $(BUILDDIR)
	$(CC) -O2 -DNDEBUG -DMI_MALLOC_OVERRIDE -I vendor/mimalloc/include -c vendor/mimalloc/src/static.c -o $(BUILDDIR)/mimalloc-core.o
	$(CC) -O2 -DNDEBUG -I vendor/mimalloc/include -c vendor/mimalloc/zc_tune.c -o $(BUILDDIR)/mimalloc-tune.o
	ld -r $(BUILDDIR)/mimalloc-core.o $(BUILDDIR)/mimalloc-tune.o -o $@

# out/zc-seed -- the bootstrap compiler built from the committed, Python-free
# seed (bootstrap/zc.c). See bootstrap/README.md and `make test-bootstrap`.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c -lm

# bin/zc -- the self-hosted compiler, bootstrapped by the seed. Persistent +
# git-ignored; rebuilt when the compiler sources change. The dev bin/zc
# self-locates to this repo (lib/system here; runtime falls back to src/runtime).
bin/zc.c: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(ZC_DEP)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c

$(BUILDDIR)/zc.o: bin/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) $(OPTFLAGS) -c bin/zc.c -o $@

bin/zc: $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zc $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) -lpthread -lm

# zc -- convenience alias for bin/zc.
zc: bin/zc

# bin/zl -- the zerolang linter + formatter (src/zl.z), built on the shared
# front-end via the compiler. A separate binary from zc so the compiler stays
# lean; zl links the front-end + typecheck (for --full's suffix rule), but never
# the emitter.
out/zl.c: $(BUILDDIR)/zc.o $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z) | bin/zc
	@mkdir -p out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c

$(BUILDDIR)/zl.o: out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zl.c -o $@

bin/zl: $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zl $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) -lpthread -lm

# bin/zls -- the zerolang language server (src/zls.z): JSON-RPC over
# stdio/--replay on the shared front-end via zcheck; no emitter. The
# lsp test kind in ztestrunner builds its own copy; this rule is the
# editor-facing binary.
out/zls.c: $(BUILDDIR)/zc.o $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z) | bin/zc
	@mkdir -p out
	bin/zc zls --src src --system lib/system $(ZCHASH) --emit-c out/zls.c

$(BUILDDIR)/zls.o: out/zls.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zls.c -o $@

bin/zls: $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zls $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) -lpthread -lm

# zl -- convenience alias for bin/zl.
zl: bin/zl

# zls -- convenience alias for bin/zls.
zls: bin/zls

# Standalone dump binaries (the Python-free golden regeneration path; the
# dumper logic lives in lib/system/zlexer.z and lib/system/zparser.z).
out/zlexer: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer --src src --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c -lm

out/zparser: bin/zc $(wildcard lib/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser --src src --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c -lm

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
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-seed bootstrap/zc.c -lm
	$(BUILDDIR)/zc-seed zc --src src --system lib/system --emit-c $(BUILDDIR)/b1.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b1 $(BUILDDIR)/b1.c -lm
	$(BUILDDIR)/zc-b1 zc --src src --system lib/system --emit-c $(BUILDDIR)/b2.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zc-b2 $(BUILDDIR)/b2.c -lm
	$(BUILDDIR)/zc-b2 zc --src src --system lib/system --emit-c $(BUILDDIR)/b3.c
	@diff $(BUILDDIR)/b2.c $(BUILDDIR)/b3.c \
		&& echo "fixpoint OK (b2 == b3)" \
		|| { echo "FAIL: seed-built compiler does not converge"; exit 1; }
	@cmp -s $(BUILDDIR)/b1.c bootstrap/zc.c \
		&& echo "seed is current (b1 == committed seed)" \
		|| echo "note: seed has lagged (b1 != committed seed) -- run 'make bump-seed' when convenient"
	$(BUILDDIR)/zc-b1 ztypes --src src --system lib/system --emit-c $(BUILDDIR)/zt.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zt $(BUILDDIR)/zt.c -lm
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
warn-check: bin/zc.c
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
PERFARGS := zc --src src --system lib/system --emit-c /dev/null
PERFRUN  := $(PERFBIN) $(PERFARGS)

# The series binary: the same emitted C as bin/zc, compiled at the series
# level by the series compiler. Depends on bin/zc because that rule is what
# emits bin/zc.c.
$(PERFBIN): bin/zc.c $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o
	@$(PERFCC) -std=c17 -w $(PERFOPT) -o $@ $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o bin/zc.c -lpthread -lm

perf: $(PERFBIN)
	@echo "== zerolang line count (.z) =="
	@lsrc=$$(cat src/*.z | wc -l); llib=$$(cat lib/system/*.z | wc -l); \
	  printf "  src/*.z: %s    lib/system/*.z: %s    total: %s\n" "$$lsrc" "$$llib" "$$((lsrc + llib))"
	@echo "== self-compile wall best-of-5 (mimalloc; drop run 1) + peak RSS =="
	@for i in 1 2 3 4 5; do /usr/bin/time -f "  %es  %MkB" $(PERFRUN) 2>&1 | tail -1; done
	@echo "== phase split (parse / typecheck / emit) =="
	@$(PERFBIN) zc --src src --system lib/system --time --emit-c /dev/null 2>&1 | tail -1 | sed 's/^/  /'
	@echo "== allocations (valgrind memcheck: total heap blocks for one self-compile) =="
	@if command -v valgrind >/dev/null 2>&1; then \
	  valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: /  /'; \
	else echo "  (valgrind not installed -- skipping alloc total)"; fi

# perf-strict -- the trustworthy allocation number. $(PERFBIN) is compiled
# here, by PERFCC at PERFOPT, so the two traps that produced wrong readings
# before -- a bin/zc silently rebuilt by `make test CC=clang`, and a driver
# built at another optimization level -- cannot reach the measurement at all;
# the .comment probe stays as a cheap assertion. The remaining guards are on
# the RUN: check the exit code (an early-aborted self-compile reads as a huge
# perf win) and require allocs == frees.
# The clang deletion is REAL behavior, not a bug: LLVM removes an allocation
# whose bytes are written but never READ and whose pointer never escapes -- a
# read of the copied bytes blocks it -- which gcc does at no tested level (an
# allocator attribute on z_xmalloc changes nothing). So a clang build is a
# different measurement, not a wrong one; `make perf-elision` measures that
# pool deliberately, and it belongs at zero.
perf-strict: $(PERFBIN)
	@readelf -p .comment $(PERFBIN) | grep -qi clang \
	  && { echo "perf-strict: $(PERFBIN) is clang-built (PERFCC=$(PERFCC)) -- refusing to measure"; exit 1; } || true
	@sha=$$(git rev-parse --short HEAD); dirty=$$(git diff --quiet && git diff --cached --quiet && echo clean || echo DIRTY); \
	  echo "== perf-strict @ $$sha ($$dirty), $(PERFCC) $(firstword $(PERFOPT)) =="
	@$(PERFRUN) > /dev/null || { echo "perf-strict: self-compile FAILED (exit $$?)"; exit 1; }
	@line=$$(valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: //'); \
	  echo "  $$line"; \
	  a=$$(echo "$$line" | sed 's/ allocs.*//;s/,//g'); f=$$(echo "$$line" | sed 's/.* allocs, //;s/ frees.*//;s/,//g'); \
	  test "$$a" = "$$f" || { echo "perf-strict: allocs != frees -- incomplete or leaking run"; exit 1; }

# perf-elision -- how much of the emitted code's allocation the C compiler
# throws away for us. LLVM deletes an allocation whose bytes are written but
# never read and whose pointer never escapes; gcc keeps it, because the memcpy
# into the buffer counts as a use. Building bin/zc.c TWICE WITH THE SAME clang
# -- once with -fno-builtin-malloc, which stops LLVM recognising the allocator
# and changes nothing else -- isolates that pool exactly: the no-builtin build
# has matched a gcc build to within 4 allocations. The delta is emitted code
# that allocates, fills a buffer, and frees it unread, so it belongs at zero;
# a growing one means a new write-only allocation crept in. Both builds are
# scratch, glibc-only (no mimalloc, so valgrind counts every block), built at
# PERFOPT so the pool stays comparable with the series, and land in $(BUILDDIR).
ELIDECC   ?= clang
NOBUILTIN := -fno-builtin-malloc -fno-builtin-free -fno-builtin-calloc -fno-builtin-realloc
perf-elision: bin/zc.c
	@command -v $(ELIDECC) >/dev/null 2>&1 || { echo "perf-elision: $(ELIDECC) not installed -- skipping"; exit 0; }
	@command -v valgrind >/dev/null 2>&1 || { echo "perf-elision: valgrind not installed -- skipping"; exit 0; }
	@sha=$$(git rev-parse --short HEAD); dirty=$$(git diff --quiet && git diff --cached --quiet && echo clean || echo DIRTY); \
	  echo "== perf-elision @ $$sha ($$dirty), $(ELIDECC) A/B over bin/zc.c =="
	@$(ELIDECC) -std=c17 -w $(PERFOPT) -o $(BUILDDIR)/zc-elide bin/zc.c -lpthread -lm
	@$(ELIDECC) -std=c17 -w $(PERFOPT) $(NOBUILTIN) -o $(BUILDDIR)/zc-noelide bin/zc.c -lpthread -lm
	@blocks() { valgrind --tool=memcheck $$1 $(PERFARGS) 2>&1 \
	    | grep 'total heap usage' | sed 's/.*usage: //;s/ allocs.*//;s/,//g'; }; \
	  kept=$$(blocks $(BUILDDIR)/zc-noelide); left=$$(blocks $(BUILDDIR)/zc-elide); \
	  test -n "$$kept" -a -n "$$left" || { echo "perf-elision: no allocation total -- a run failed"; exit 1; }; \
	  printf "  as emitted:            %s allocs\n" "$$kept"; \
	  printf "  after LLVM deletion:   %s allocs\n" "$$left"; \
	  printf "  write-only pool:       %s\n" "$$((kept - left))"

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
# anyval or AnyRef. Two stdlib files keep counted residuals:
#   system.z (5) -- `return` and `typedef`, whose parameter is never consulted
#     (probed: bounding them to anyval does not reject a reftype), plus
#     `Iterator` and `OptionView`, which still span both families.
#   collections.z (0) -- the P5 split is complete: every container template
#     names the family it takes.
# Both are ratchets: they may only DECREASE. Enforced here rather than in the
# typechecker because generic-param registration has no unit name in hand.
any-guard:
	@u=$$(grep -rn 'Any\.generic' --include=*.z src examples tests | grep -vE ':[0-9]+: *#' | grep -vE 'any_bound_retired\.z|any_shadow_bound\.z' | wc -l); \
	if [ "$$u" -gt 0 ]; then \
	  echo "any-guard FAIL: $$u use(s) of Any.generic in user source"; \
	  grep -rn 'Any\.generic' --include=*.z src examples tests | grep -vE ':[0-9]+: *#' | grep -vE 'any_bound_retired\.z|any_shadow_bound\.z'; \
	  echo "  A generic must name the family it takes: anyval.generic or AnyRef.generic."; \
	  exit 1; \
	fi; \
	s=$$(grep -c 'Any\.generic' lib/system/system.z); \
	c=$$(grep -c 'Any\.generic' lib/system/collections.z); \
	fail=0; \
	if [ "$$s" -gt 5 ]; then echo "any-guard FAIL: system.z Any.generic = $$s (baseline 5)"; fail=1; fi; \
	if [ "$$c" -gt 0 ]; then echo "any-guard FAIL: collections.z Any.generic = $$c (baseline 0)"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then echo "  Lower the baseline here when a residual is legitimately removed."; exit 1; fi; \
	m=$$(grep -lE 'Any\.generic|\.lock[^A-Za-z0-9_]' tests/fixtures/lsp_cases/*.msgs 2>/dev/null | wc -l); \
	if [ "$$m" -gt 0 ]; then \
	  echo "any-guard FAIL: $$m lsp .msgs inline source(s) spell Any.generic or a .lock marker"; \
	  grep -lE 'Any\.generic|\.lock[^A-Za-z0-9_]' tests/fixtures/lsp_cases/*.msgs; \
	  echo "  didOpen inline text overrides the workspace file -- migrate the .msgs too."; \
	  exit 1; \
	fi; \
	echo "any-guard OK: user source clean; system.z=$$s (<=5) collections.z=$$c (<=0); lsp .msgs clean"

shadow-guard:
	@n1=$$(grep -c 'cTypeOf name:' src/zemitterc.z); \
	n2=$$(grep -c 'cTypeForName symtab:' src/zemitterc.z); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "shadow-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "shadow-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "'cTypeOf name:'" "$$n1" 17; \
	chk "'cTypeForName symtab:'" "$$n2" 0; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new by-name C-type site was added. Resolve the C type from the canonical"; \
	  echo "  type id via scalarCTypeFor / cTypeForNameTid / typeRefC, not cTypeOf(name)."; \
	  echo "  (If a site was legitimately removed, lower the baseline here instead.)"; \
	  exit 1; \
	fi; \
	echo "shadow-guard OK: cTypeOf name:=$$n1 (<=17)  cTypeForName symtab:=$$n2 (<=0)"

# emitter-guard -- ratchet against name-resolution creep in the C emitter. The
# de-lookup arc drove these to their current floors: the emitter reads
# typechecker stamps and canonical ids; every remaining by-name resolution is a
# counted residual (template re-emission, probe-chain legs). A rising count
# means a new name-resolved site -- resolve from stamps/ids instead, or lower
# the baseline when a residual is legitimately removed. typeNameOfReg9 went
# 91 -> 92 for the arm-alias leg's scalar test: `scalarCTypeFor` is the
# shadow-SAFE wrapper (it re-checks the tid for a user shadow) and it needs
# the type's name, so the name lookup is the sanctioned shape here.
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
	chk() { if [ "$$2" -gt "$$3" ]; then echo "emitter-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "emitter-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "composeCname in src/ztypes.z" "$$g1" 0; \
	chk "'z_t{' literals in src/zemitterc.z" "$$g2" 3; \
	chk "ztypecheck.resolvedByKey" "$$e1" 12; \
	chk "ztypecheck.walkLookupTyperef" "$$e2" 5; \
	chk "resolveTypeIdByName" "$$e3" 22; \
	chk "userFnId" "$$e4" 35; \
	chk "childOwnershipText" "$$e5" 0; \
	chk "typeNameOfReg9" "$$e6" 92; \
	chk "ztypes.mangleVarName (both inside varCName)" "$$e7" 2; \
	chk "io.readText" "$$e8" 5; \
	chk "monoOriginName" "$$e9" 8; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  ctxCname instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: resolvedByKey=$$e1 walkLookup=$$e2 resolveByName=$$e3 userFnId=$$e4 ownText=$$e5 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 monoOrigin=$$e9"

# deadcode-guard -- ratchet on emitted statements that no path can reach. clang's
# -Wunreachable-code family is the oracle; gcc accepts the flag but never warns.
# Every example and corpus program is emitted and counted, so a new dead-code
# shape anywhere raises the number, not just one in the dedicated fixture
# (tests/fixtures/emitc_corpus/deadcode_shapes.z, which must stay at zero).
#
# A rise means an emitting site appended a statement after a block that had
# already diverged. The fix is to ask blockDiverges (src/zemitterc.z) about the
# block you just emitted and skip the append -- never to read a flag left over
# from someone else's block, which conflates an inner `if c then { break }` with
# its enclosing scope and drops a live cleanup.
#
# Every divergence case is closed: blockDiverges models a return (valued or
# bare), a `never` stamp, an if/else chain whose every arm diverges, and a
# `for true loop` with no break, and emitBodyNode stops emitting after any
# statement that diverges.
#
# Nine of the ten that remain are a folding gap rather than a missing divergence
# check: docs/spec.pdoc:5652 bounds constant folding to INTEGER literals and
# constants in STATEMENT position, so a bool or float condition, an `if` in value
# position, and the statements after an `if` the emitter folded to an
# unconditional branch all still emit code no path runs. Closing them means
# widening the folder, which is a feature, not a cleanup.
#
# Every divergence case is closed. Divergence is the typechecker's `never`
# stamp and nothing else: checkCase stamps it on an exhaustive match whose every
# arm -- else included -- diverges, and a panic or diverging call carries it
# because it does not return. The emitter reads that stamp and does no
# shape-matching of its own. It used to be wrong, not because the stamp is the
# wrong idea but because checkCase left the else arm out of the all-diverge
# test, so a match whose else completed still stamped `never`; an enclosing arm
# then lost its C `break;` and the switch fell through
# (tests/fixtures/emitc_corpus/match_arm_fallthrough.z: 1 instead of 9).
#
# The ten that remain are not conforming output. docs/spec.pdoc:5616 promises,
# without qualification, that a constant-condition `if` does not emit its false
# branch; the "bounded to integer literals" clause at :5652 bounds what the
# `error` builtin can prove REACHABLE, which is a different question. So these
# are gaps, not decisions:
#
# The baseline is ZERO: the emitter emits no statement that no path can reach,
# across every example and corpus program. Keep it there. A rise is a real
# regression, not a number to bump -- the two ways it happens are appending
# after a block that has already diverged (ask blockDiverges about the block you
# just emitted) and emitting a constant-condition branch that spec.pdoc:5616
# promises not to emit (fold it, in whatever position it appears).
# The other class -- a statement after something already diverged -- is closed:
# both statement walks stop on a diverging statement, and ifDiverges models a
# chain whose conditions all fold, where only the branch the emitter actually
# emits decides.
#
# case-guard: a program declaring `main` is an entry point, so some case list has to
# compile it -- run_cases (build + compare a golden), smoke_cases (build, output not
# compared) or dump_cases. A unit with NO main exists to be opened by another program and
# is compiled through its dependant, so it is exempt by construction rather than by a list.
# record_method_ref_default was in no list at all: it emitted C that gcc rejected, and
# nothing noticed until a backend sweep compiled every emitted file by hand.
case-guard:
	@fail=0; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  grep -qE '^main: *function' $$f || continue; \
	  awk -v b="$$b" '$$1 == b {found = 1} END {exit !found}' \
	    tests/fixtures/run_cases.txt tests/fixtures/smoke_cases.txt tests/fixtures/dump_cases.txt \
	    || { echo "case-guard: $$b declares main but is in no case list -- nothing compiles it"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then \
	  echo "  add it to run_cases.txt with a tests/fixtures/run_golden/<name>.out, or to"; \
	  echo "  smoke_cases.txt when the output is environment-dependent (build only)"; \
	  exit 1; \
	fi; \
	echo "case-guard OK: every program declaring main is in a case list"

# Lower the baseline as each folder gap is closed. Skipped when clang is absent -- clang is
# not a build requirement.
DEADCODE_BASELINE := 0

deadcode-guard: bin/zc
	@command -v clang >/dev/null 2>&1 || { echo "deadcode-guard SKIP: clang not installed"; exit 0; }; \
	d=$$(mktemp -d); n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  bin/zc emit $$f -o $$d/$$b.c 2>/dev/null || continue; \
	  c=$$(clang -fsyntax-only -std=c17 -Wunreachable-code -Wunreachable-code-break \
	       -Wunreachable-code-return $$d/$$b.c 2>&1 | grep -c 'unreachable-code'); \
	  n=$$(($$n + $$c)); \
	  if [ "$$c" -gt 0 ]; then rep="$$rep  $$b: $$c\n"; fi; \
	done; \
	rm -rf $$d; \
	if [ "$$n" -gt "$(DEADCODE_BASELINE)" ]; then \
	  echo "deadcode-guard FAIL: $$n unreachable statements (baseline $(DEADCODE_BASELINE))"; \
	  printf "$$rep"; \
	  echo "  A site appended after a diverged block. See blockDiverges in src/zemitterc.z."; \
	  exit 1; \
	elif [ "$$n" -lt "$(DEADCODE_BASELINE)" ]; then \
	  echo "deadcode-guard: $$n < baseline $(DEADCODE_BASELINE) -- lower DEADCODE_BASELINE here"; \
	else \
	  echo "deadcode-guard OK: $$n unreachable statements (baseline $(DEADCODE_BASELINE))"; \
	fi

# eager-guard -- `--eager` resolves every definition of every unit, not only the
# ones a use site demands, so an error inside a definition nothing references is
# still reported. Nothing else exercises the mode; without this it would rot.
# Every corpus program must be clean in BOTH modes.
eager-guard: bin/zc
	@n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  if ! bin/zc emit $$f --eager -o /dev/null >/dev/null 2>&1; then \
	    n=$$(($$n + 1)); rep="$$rep  $$b\n"; \
	  fi; \
	done; \
	if [ "$$n" -gt 0 ]; then \
	  echo "eager-guard FAIL: $$n program(s) error under --eager"; \
	  printf "$$rep"; \
	  echo "  An error inside a definition nothing references -- fix it, do not drop the guard."; \
	  exit 1; \
	fi; \
	echo "eager-guard OK: examples + corpus clean under --eager"

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
# container templates, and C the emitter builds from string literals (ListRef.get,
# .contains, .listview, .sort, .iterate, ListIter.call, MapRR.getv). So a receiver
# is found by TYPE rather than by parameter name -- the first parameter whose
# struct is the function's own prefix, which covers self / _this / _it / _e /
# s / a alike. VIEW_GUARD_PLACEHOLDER names the type each template placeholder
# stands for; an unmapped placeholder names a user type or a valtype (array,
# str, the protocol vtable, meta.create), and `.view` does not apply to either.
# An emitter-built function names a runtime mono rather than a type the guard
# can read, so VIEW_GUARD_EMITTED says which declaration each one backs; `-`
# marks the ones backing no reference-type method at all (array / str value
# equality, a union destructor, ListRef.extendView).
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

VIEW_GUARD_PLACEHOLDER := z_List.c.tmpl=@@NAME@@:ListRef z_Map.c.tmpl=@@NAME@@:MapRR \
  z_MapIter.c.tmpl=@@NAME@@:MapRR,@@MAPKEYITER@@:MapKeyIter,@@MAPITEMITER@@:MapItemIter,@@MAPENTRY@@:MapEntry \
  z_Set.c.tmpl=@@NAME@@:SetRef,@@SETITER@@:SetIter
VIEW_GUARD_EMITTED := get:ListRef.get,ListView.get getMut:ListRef.getMut,ListView.getMut \
  contains:ListRef.contains \
  listview:ListRef.listview sort:ListRef.sort iterate:ListRef.iterate \
  call:ListIter.call,ListIterVal.call \
  iterateMut:ListRef.iterateMut getv:MapRR.get eq:- extendView:- destroy:- \
  hasv:MapRR.has,SetRef.has deletev:SetRef.delete
VIEW_GUARD_BACKS := StringView.eq===,!= StringView.cmp=compare,<,<=,>,>=
VIEW_GUARD_INTERNAL := String.cat String.print String.free String.eq String.cmp \
  StringView.print StringView.indexOfRaw StringView.replaceImpl \
  ListRef.destroy ListRef.grow MapRR.destroy MapRR.grow MapRR.find \
  SetRef.destroy SetRef.grow SetRef.find MapEntry.key MapEntry.value
VIEW_GUARD_INLINE := Bytes.byteview:unemitted \
  StringView.asString:inline \
  ListVal.append:ListRef.append ListVal.insert:ListRef.insert \
  ListVal.extend:ListRef.extend ListVal.get:ListRef.get ListVal.set:ListRef.set \
  ListVal.pop:ListRef.pop ListVal.contains:ListRef.contains \
  ListVal.getMut:ListRef.getMut \
  ListViewVal.get:ListView.get ListViewVal.getMut:ListView.getMut \
  ListViewVal.length:inline \
  ListVal.sort:ListRef.sort ListVal.listview:ListRef.listview \
  ListVal.iterate:ListRef.iterate ListVal.iterateMut:ListRef.iterateMut \
  ListVal.length:inline ListVal.capacity:inline \
  SetVal.add:SetRef.add SetVal.has:SetRef.has SetVal.delete:SetRef.delete \
  SetVal.iterate:SetRef.iterate SetIterVal.call:SetIter.call \
  SetVal.length:inline SetVal.capacity:inline \
  MapRV.get:MapRR.get MapRV.set:MapRR.set MapRV.has:MapRR.has \
  MapRV.remove:MapRR.remove MapRV.iterate:MapRR.iterate \
  MapRV.iterateItems:MapRR.iterateItems \
  MapRV.length:inline MapRV.capacity:inline \
  MapVR.get:MapRR.get MapVR.set:MapRR.set MapVR.has:MapRR.has \
  MapVR.remove:MapRR.remove MapVR.iterate:MapRR.iterate \
  MapVR.iterateItems:MapRR.iterateItems \
  MapVR.length:inline MapVR.capacity:inline \
  MapVV.get:MapRR.get MapVV.set:MapRR.set MapVV.has:MapRR.has \
  MapVV.remove:MapRR.remove MapVV.iterate:MapRR.iterate \
  MapVV.iterateItems:MapRR.iterateItems \
  MapVV.length:inline MapVV.capacity:inline \
  MapKeyIterRV.call:MapKeyIter.call MapKeyIterVR.call:MapKeyIter.call \
  MapKeyIterVV.call:MapKeyIter.call \
  MapItemIterRV.call:MapItemIter.call MapItemIterVR.call:MapItemIter.call \
  MapItemIterVV.call:MapItemIter.call \
  ListRef.length:inline ListRef.capacity:inline ListView.length:inline \
  MapRR.length:inline MapRR.capacity:inline SetRef.length:inline SetRef.capacity:inline \
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
	@fail=0; conv=""; \
	for u in io os cli net; do \
	  for n in $$(awk '/^[a-zA-Z][a-zA-Z0-9]*: function/ {name=$$1; sub(/:.*/,"",name); pending=1} pending && /is native/ {print name; pending=0} pending && /is \{/ {pending=0}' lib/system/$$u.z); do \
	    case " $(NATIVE_GUARD_EXCEPTIONS) " in *" $$u.$$n "*) continue;; esac; \
	    snake=$$(echo "$$n" | sed 's/\([A-Z]\)/_\1/g' | tr 'a-z' 'A-Z'); \
	    frag="_Z_$$(echo $$u | tr 'a-z' 'A-Z')_$$snake"; \
	    conv="$$conv $$frag"; \
	    test -f src/runtime/natives/$$frag.inc || { echo "native-guard: $$u.$$n declared native but $$frag.inc missing (add the fragment or an exceptions entry)"; fail=1; }; \
	  done; \
	done; \
	for f in $$(grep -oE '"_Z_[A-Z0-9_]+"' src/zemitterc.z | tr -d '"' | sort -u); do \
	  test -f src/runtime/natives/$$f.inc || { echo "native-guard: fragment $$f.inc referenced but missing"; fail=1; }; \
	done; \
	for f in src/runtime/natives/*.inc; do \
	  stem=$$(basename $$f .inc); \
	  case " $$conv " in *" $$stem "*) continue;; esac; \
	  need=$$(echo "$$stem" | sed 's/^_Z_//' | tr 'A-Z' 'a-z'); \
	  grep -qF "\"$$stem\"" src/zemitterc.z || grep -qF "\"$$need\"" src/zemitterc.z || { echo "native-guard: $$stem.inc on disk but nothing references it (orphan -- delete it or load it)"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "native-guard OK: native declarations and runtime fragments consistent (incl. no orphans)"
