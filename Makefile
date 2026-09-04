CC       := gcc
# -Wdiscarded-qualifiers is the GCC spelling; clang groups the same
# diagnostic under -Wincompatible-pointer-types-discards-qualifiers.
QUALWERR := $(if $(findstring clang,$(shell $(CC) --version 2>/dev/null | head -1)),-Werror=incompatible-pointer-types-discards-qualifiers,-Werror=discarded-qualifiers)
CFLAGS_BASE := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types \
            -Werror=unused-but-set-variable
CFLAGS   := $(CFLAGS_BASE) $(QUALWERR)

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
# the -l flags a driver's own emitted C declares on its fixed second line.
# bin/zc reaches lib/system/tcc.z and so needs -ldl; zl and zls do not. Read
# off the artifact rather than hardcoded, exactly as $(EXDIR)/%.bin does.
# ZLINKSED lifts the names out of an emitted C file's `zlink:` header; ZLINKOF
# turns them into -l flags. SEARCHED in the first few lines rather than pinned
# to line 2: an emitter change that inserted a line above it would otherwise
# yield NO libraries, silently and with no error anywhere. One definition, so
# the three readers cannot drift.
ZLINKSED = sed -n '1,8s|^/\* zlink: \(.*\) \*/$$|\1|p'
ZLINKOF = $$($(ZLINKSED) $(1) | tr ' ' '\n' | sed -e '/^$$/d' -e 's|^|-l|')
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
# into a measurement. See "Toolchain findings" in docs/perf-baseline.md.
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
# The hand-written runtime the emitter inlines into every file it emits
# (fragments, per-family templates and the native table). An edit here changes
# emitted C exactly as a source edit does, so the emitted artifacts depend on
# it -- without that, a fragment change quietly does not reach the binaries.
RT_DEP  := $(wildcard src/runtime/*.inc) $(wildcard src/runtime/*.c.tmpl) $(wildcard src/runtime/natives/*.inc) src/runtime/natives.tbl

# install tree (GOROOT-style). Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# all .z files in examples/ (exclude library-only modules without main)
SKIP     := mathutil genmath dissectlib
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: emit-set ident-set natives-tbl-guard generic-param-guard const-row-guard all check test ci ci-corpus build clean style-lint style-lint-fast zc zl zls tcc install regen-goldens bump-seed test-bootstrap docs warn-check perf shadow-guard emitter-guard native-guard fallback-guard member-guard highlight-guard deadcode-guard require-guard static-tcc-guard refusal-guard zlink-rules-guard test-tcc test-tcc-heavy mode-parity readable-check user-native-guard perf-strict perf-elision pre-push

# Keep pattern-chain intermediates (the per-example .c files) for debugging.
.SECONDARY:

# ZLSCOPE -- what the zl *linter* checks: the tool + compiler sources, and every unit
# under lib/system -- which is the stdlib proper (io/os/collections/system/cli/core) as
# well as the relocated front-end, because they share that directory. What it does NOT
# reach is examples/ and tests/fixtures/; a rule that must hold there needs its own guard.
ZLSCOPE := src/*.z lib/system/*.z lib/system/system/*.z tests/unit/*.z
# FMTSCOPE -- what the zl *formatter* checks: fmt applies only whitespace/colon fixes (no
# elide-label issue), so it covers the whole codebase, keeping every .z consistently formatted.
FMTSCOPE := src/*.z lib/system/*.z lib/system/system/*.z examples/*.z

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
$(BUILDDIR)/ztestrunner: bin/zc src/ztestrunner.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c $(call ZLINKOF,$(BUILDDIR)/ztestrunner.c) -lm

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
ci: style-lint warn-check shadow-guard emitter-guard native-guard alias-label-guard fwd-shape-guard generic-param-guard natives-tbl-guard const-row-guard view-guard fallback-guard member-guard highlight-guard any-guard deadcode-guard eager-guard case-guard user-native-guard zlink-guard zlink-rules-guard require-guard static-tcc-guard refusal-guard readable-check test-tcc-heavy mode-parity ci-corpus
	$(MAKE) --no-print-directory test-bootstrap BOOTSTRAP_CCS="$(CI_BOOTSTRAP_CCS)"
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

ci-corpus: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

# what a corpus run under the vendored tcc needs built before it starts.
TCC_RUN_DEPS := bin/zc $(BUILDDIR)/tcc $(BUILDDIR)/ztestrunner

# test-tcc -- the vendored tcc compiles the corpus. --cc-forward is what makes
# this a test of the tcc BACKEND and not merely of tcc-the-C-compiler: zc folds
# `platform.cc` during type checking, so quadfloat's `require:` guard fires and
# its two programs are rejected by name instead of dying in tcc's parser.
# tests/tcc-known-failures.txt records the split, program and stage; a move in
# EITHER direction fails, so gaining a guard is a deliberate edit there.
#
# TWO TIERS, the same split `test` and `ci-corpus` already use for gcc, and for
# the same reason: the fast one is 4s and belongs in the edit loop, the heavy
# one is 6m23s and belongs in ci. `test-tcc-heavy` adds the self-host and
# fixpoint kinds, and that is where it earns its keep -- the self-host leg
# builds zc ITSELF under the sanitizer and runs it over every unit, and tcc's
# bounds checker reports every block still LIVE at exit where ASan reports only
# what is definitely LOST. The gcc heavy leg cannot see what that one sees.
# One recipe and one ratchet serve both; only the flag differs.
TCC_KNOWN := tests/tcc-known-failures.txt
# empty for the fast tier; test-tcc-heavy overrides it.
TCC_TIER ?=

test-tcc: $(TCC_RUN_DEPS)
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(BUILDDIR)/tcc --cc-forward \
	   --ccflags "-B $(TCCLIB)" --root . $(TCC_TIER) --jobs $(NPROC) \
	   > $(BUILDDIR)/tcc-run.log 2>&1; \
	awk '/^FAIL/ { k = $$1; sub(/^FAIL\(/, "", k); sub(/\)$$/, "", k); \
	       print $$2, (NF >= 3 ? $$3 : "(" k ")") }' $(BUILDDIR)/tcc-run.log \
	  | sort > $(BUILDDIR)/tcc-fails.txt; \
	grep -v '^#' $(TCC_KNOWN) | grep -v '^ *$$' | sort > $(BUILDDIR)/tcc-known.txt; \
	if ! diff -u $(BUILDDIR)/tcc-known.txt $(BUILDDIR)/tcc-fails.txt; then \
	  echo "test-tcc FAIL: the tcc failure set moved (-known +actual above)"; \
	  echo "  a gained line is a regression; a lost line, or one moving from (cc)"; \
	  echo "  to (zc), means a unit now guards itself -- record it in $(TCC_KNOWN)"; \
	  echo "  in the same commit. Full log: $(BUILDDIR)/tcc-run.log"; \
	  exit 1; \
	fi; \
	echo "test-tcc$(TCC_TIER:--heavy=-heavy) OK: $$(grep -c . $(BUILDDIR)/tcc-fails.txt) known failures, none new"

# test-tcc-heavy -- the same corpus and the same ratchet, plus the self-host and
# fixpoint kinds. In ci, not in the edit loop: 6m23s against test-tcc's 4s.
#
# IT CARRIES test-tcc's PREREQUISITES EVEN THOUGH THE SUB-MAKE WOULD BUILD
# THEM. A recursive make is a SECOND SCHEDULER: it decides for itself what is
# out of date, and it cannot see what the parent is already building. With no
# prerequisites of its own this target was eligible immediately, so `make ci -j`
# ran it beside the guards -- and the parent linked bin/zc for them while the
# sub-make linked bin/zc for this one. Two processes writing the binary that
# every guard was executing. Measured: `make -j8 test-tcc-heavy require-guard`
# after touching a source linked bin/zc TWICE.
#
# Naming them here makes the parent build them once, in order, before the
# sub-make starts; the sub-make then finds everything current and only runs the
# recipe. ONE list, shared with test-tcc, so the two cannot drift apart -- a
# copy that fell behind would put the race back without changing a line here.
test-tcc-heavy: $(TCC_RUN_DEPS)
	@$(MAKE) --no-print-directory test-tcc TCC_TIER=--heavy

# readable-check -- --readable-names is a debug affordance, so nothing else
# exercises it; without this it would rot unnoticed. The two schemes differ
# only in identifier spelling, so the built programs must behave identically.
# Cases are `name:srcdir`. `shadow_unit_const` and `rn_sibling_shadow` are here
# rather than in the corpus alone because the scheme is what the cases are
# about: readable names spell a local by its SOURCE name, so a local shadowing
# another unit's constant, or two sibling blocks binding one name, only diverge
# under this flag. The COMPILER leg below is the one that matters most: the
# small programs exercise a handful of locals each, and a naming scheme is only
# proven by a program with tens of thousands of them.
readable-check: bin/zc $(BUILDDIR)/buildstamp.o
	@mkdir -p $(BUILDDIR)/rn
	@for c in hello:examples vector:examples records:examples fibonacci:examples \
	          typedefs:examples shadow_unit_const:tests/fixtures/emitc_corpus \
	          rn_sibling_shadow:tests/fixtures/emitc_corpus; do \
	  n=$${c%%:*}; d=$$(echo $$c | sed 's/^[^:]*://'); \
	  bin/zc $$n --src $$d --system lib/system --emit-c $(BUILDDIR)/rn/$$n-id.c || exit 1; \
	  bin/zc $$n --src $$d --system lib/system --readable-names --emit-c $(BUILDDIR)/rn/$$n-rn.c || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-id $(BUILDDIR)/rn/$$n-id.c $(call ZLINKOF,$(BUILDDIR)/rn/$$n-id.c) -lm || exit 1; \
	  $(CC) $(CFLAGS) -o $(BUILDDIR)/rn/$$n-rn $(BUILDDIR)/rn/$$n-rn.c $(call ZLINKOF,$(BUILDDIR)/rn/$$n-rn.c) -lm || exit 1; \
	  $(BUILDDIR)/rn/$$n-id > $(BUILDDIR)/rn/$$n-id.out 2>&1; \
	  $(BUILDDIR)/rn/$$n-rn > $(BUILDDIR)/rn/$$n-rn.out 2>&1; \
	  diff -q $(BUILDDIR)/rn/$$n-id.out $(BUILDDIR)/rn/$$n-rn.out > /dev/null \
	    || { echo "readable-check FAIL: $$n differs between naming schemes"; exit 1; }; \
	done
	bin/zc zc --src src --system lib/system --readable-names --emit-c $(BUILDDIR)/rn/zc-rn.c
	$(CC) $(CFLAGS) -c $(BUILDDIR)/rn/zc-rn.c -o $(BUILDDIR)/rn/zc-rn.o
	$(CC) -o $(BUILDDIR)/rn/zc-rn $(BUILDDIR)/rn/zc-rn.o $(BUILDDIR)/buildstamp.o \
	  $(call ZLINKOF,$(BUILDDIR)/rn/zc-rn.c) -lpthread -lm
	@$(BUILDDIR)/rn/zc-rn hello --src examples --system lib/system --emit-c $(BUILDDIR)/rn/hello-by-rn.c
	@bin/zc hello --src examples --system lib/system --emit-c $(BUILDDIR)/rn/hello-by-id.c
	@cmp $(BUILDDIR)/rn/hello-by-id.c $(BUILDDIR)/rn/hello-by-rn.c \
	  || { echo "readable-check FAIL: the readable-named compiler emits different C"; exit 1; }
	@echo "readable-check OK: --readable-names builds and runs identically (the compiler included)"

# compile all examples: .z -> .c -> binary, one pattern-rule chain per example
# so -j fans out the emits and gcc's. Binaries land in $(BUILDDIR)/ex/.
EXDIR  := $(BUILDDIR)/ex
EXBINS := $(NAMES:%=$(EXDIR)/%.bin)

$(EXDIR)/%.c: examples/%.z bin/zc
	@mkdir -p $(EXDIR)
	bin/zc $* --src examples --system lib/system --emit-c $@

# the emitted C names the libraries its reached units declared on its fixed
# second line, so the link follows the program rather than a blanket flag.
$(EXDIR)/%.bin: $(EXDIR)/%.c
	$(CC) $(CFLAGS) -o $@ $< \
	  $(call ZLINKOF,$<) -lm

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

# out/tcc + out/tcc-lib -- the vendored tinycc (vendor/tinycc), built by
# UPSTREAM's Makefile from a staged copy rather than by rules of our own:
# libtcc1.a is produced by the freshly built tcc through tcc's lib/Makefile,
# which includes its root Makefile, so a hand-rolled object list would have to
# track upstream by hand. Own flags, third-party code. This is NOT on the
# bin/zc path -- `make bin/zc` never builds it; test-tcc, install and ci do.
#
# GITHASH=no -- upstream stamps `git rev-parse` output into tcc.o, which from a
# staged copy inside THIS repo would bake zerolang's branch and dirty flag into
# `tcc -v`. CPPFLAGS=-fPIC rather than CFLAGS= (which would clobber
# config.mak's): the driver and libtcc.so share one set of objects and only
# libtcc.so carries -fPIC upstream, so without this the objects' flags depend
# on which target make reaches first. Sequential -j1 sub-makes: libtcc1.a needs
# the built tcc, and upstream documents a c2str/tccdefs_.h race under -j.
#
# libtcc.so is staged INTO the payload dir beside libtcc1.a and include/, so
# one resolved directory answers every question zc has and `install` is one cp.
TCC_TRIPLE ?= linux-x86_64
TCC_CONFIGDIR := vendor/tinycc/config/$(TCC_TRIPLE)
TCC_SRC := $(wildcard vendor/tinycc/src/*.c vendor/tinycc/src/*.h \
                      vendor/tinycc/src/Makefile vendor/tinycc/src/lib/* \
                      vendor/tinycc/src/include/*)
TCC_MAKE = $(MAKE) -C $(BUILDDIR)/tinycc -j1 GITHASH=no CPPFLAGS=-fPIC
TCCLIB := $(BUILDDIR)/tcc-lib

$(BUILDDIR)/tcc: $(TCC_SRC) $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak
	@mkdir -p $(BUILDDIR)
	rm -rf $(BUILDDIR)/tinycc $(TCCLIB)
	mkdir -p $(BUILDDIR)/tinycc $(TCCLIB)
	cp -r vendor/tinycc/src/. $(BUILDDIR)/tinycc/
	cp $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak $(BUILDDIR)/tinycc/
	$(TCC_MAKE) tcc
	$(TCC_MAKE) libtcc.so
	$(TCC_MAKE) libtcc1.a
	cp $(BUILDDIR)/tinycc/libtcc1.a $(BUILDDIR)/tinycc/libtcc.so $(TCCLIB)/
	cp $(BUILDDIR)/tinycc/runmain.o $(BUILDDIR)/tinycc/bt-exe.o \
	   $(BUILDDIR)/tinycc/bt-log.o $(BUILDDIR)/tinycc/bcheck.o $(TCCLIB)/
	cp -r vendor/tinycc/src/include $(TCCLIB)/include
	cp $(BUILDDIR)/tinycc/tcc $@

# the payload comes out of the same recipe; the driver is its stamp.
$(TCCLIB)/libtcc.so: $(BUILDDIR)/tcc ;

tcc: $(BUILDDIR)/tcc $(TCCLIB)/libtcc.so
	@echo "vendored tcc: $(BUILDDIR)/tcc (payload $(TCCLIB))"

# out/zc-seed -- the bootstrap compiler built from the committed, Python-free
# seed (bootstrap/zc.c). See bootstrap/README.md and `make test-bootstrap`.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c $(call ZLINKOF,bootstrap/zc.c) -lm

# bin/zc -- the self-hosted compiler, bootstrapped by the seed. Persistent +
# git-ignored; rebuilt when the compiler sources change. The dev bin/zc
# self-locates to this repo (lib/system here; runtime falls back to src/runtime).
bin/zc.c: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z) $(ZC_DEP) $(RT_DEP)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c

$(BUILDDIR)/zc.o: bin/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) $(OPTFLAGS) -c bin/zc.c -o $@

bin/zc: $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zc $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,bin/zc.c) -lpthread -lm

# zc -- convenience alias for bin/zc.
zc: bin/zc

# bin/zl -- the zerolang linter + formatter (src/zl.z), built on the shared
# front-end via the compiler. A separate binary from zc so the compiler stays
# lean; zl links the front-end + typecheck (for --full's suffix rule), but never
# the emitter.
out/zl.c: $(BUILDDIR)/zc.o $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z) $(RT_DEP) | bin/zc
	@mkdir -p out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c

$(BUILDDIR)/zl.o: out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zl.c -o $@

bin/zl: $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zl $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zl.c) -lpthread -lm

# bin/zls -- the zerolang language server (src/zls.z): JSON-RPC over
# stdio/--replay on the shared front-end via zcheck; no emitter. The
# lsp test kind in ztestrunner builds its own copy; this rule is the
# editor-facing binary.
out/zls.c: $(BUILDDIR)/zc.o $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z) $(RT_DEP) | bin/zc
	@mkdir -p out
	bin/zc zls --src src --system lib/system $(ZCHASH) --emit-c out/zls.c

$(BUILDDIR)/zls.o: out/zls.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zls.c -o $@

bin/zls: $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zls $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zls.c) -lpthread -lm

# zl -- convenience alias for bin/zl.
zl: bin/zl

# zls -- convenience alias for bin/zls.
zls: bin/zls

# The dump tools behind the goldens: tests/unit/zlexer_dump.z and
# tests/unit/zparser_dump.z, programs over the front-end units.
out/zlexer: bin/zc tests/unit/zlexer_dump.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer_dump --src tests/unit --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c $(call ZLINKOF,$(BUILDDIR)/zlexer.c) -lm

out/zparser: bin/zc tests/unit/zparser_dump.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser_dump --src tests/unit --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c $(call ZLINKOF,$(BUILDDIR)/zparser.c) -lm

# Regenerate the lexer / parser / whole-program goldens from the dump tools.
# Always review the resulting diff before committing.
regen-goldens: out/zlexer out/zparser
	@for f in examples/*.z; do \
		name=$$(basename $$f .z); \
		$(BUILDDIR)/zlexer $$f > tests/fixtures/lexer_golden/$$name.tokens; \
		$(BUILDDIR)/zparser $$f > tests/fixtures/parser_golden/$$name.ast; \
	done
	@for f in tests/fixtures/zlexer_z/*.z; do \
		name=$$(basename $$f .z); \
		$(BUILDDIR)/zlexer $$f > tests/fixtures/zlexer_z/$$name.tokens; \
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

# BOOTSTRAP_CCS -- the compilers test-bootstrap proves the seed against. The
# default is whatever $(CC) names, which keeps the local loop to one chain;
# `ci` passes all three, because "no Python, any C compiler" is a claim about a
# SET of compilers and gcc alone cannot support it.
BOOTSTRAP_CCS ?= $(CC)

# CI_BOOTSTRAP_CCS -- what `ci` proves the seed against. Overridable, but a
# narrower list is a narrower CLAIM: test-bootstrap refuses a compiler it
# cannot run rather than quietly proving less than the banner says.
CI_BOOTSTRAP_CCS ?= $(CC) clang $(BUILDDIR)/tcc

# test-bootstrap -- prove the committed seed bootstraps a correct compiler with
# NO Python: cc the seed, double-bootstrap and assert the fixpoint (b2 == b3),
# plus a correctness check (a seed-built compiler builds ztypes to its golden).
# Slow (3 zc.c compiles per compiler; tcc's whole chain is ~0.4s, clang's is the
# expensive one).
#
# Run per compiler in BOOTSTRAP_CCS, and then across them: the emitted C must be
# BYTE-IDENTICAL whichever compiler built the compiler that emitted it. That is
# the real content of the claim. Three toolchains each producing *a* working zc
# would still allow three different zcs; b1(gcc) == b1(clang) == b1(tcc) says
# they are the same compiler, and it is the assertion that would catch a
# codegen-dependent emission -- an uninitialised read, a pointer printed, an
# iteration order that depends on layout.
#
# gcc and clang get the project's CFLAGS. The vendored tcc gets -B, which it
# cannot resolve its own headers without, and NONE of the -Werror= pins, which
# it accepts and does not implement: a harness never hands a compiler a flag
# whose effect it has not established.
test-bootstrap:
	@mkdir -p $(BUILDDIR)
	@set -e; \
	for cc in $(BOOTSTRAP_CCS); do \
	  tag=$$(basename $$cc); \
	  case "$$cc" in *tcc*) f="-B $(TCCLIB)";; *) f="$(CFLAGS)";; esac; \
	  if ! command -v $$cc >/dev/null 2>&1 && [ ! -x $$cc ]; then \
	    echo "test-bootstrap FAIL: '$$cc' is not available"; \
	    echo "  BOOTSTRAP_CCS names the toolchains this claim is proved against, so a"; \
	    echo "  missing one is a smaller claim, not a smaller run. Install it, or narrow"; \
	    echo "  the list deliberately: make test-bootstrap BOOTSTRAP_CCS='gcc'"; \
	    exit 1; \
	  fi; \
	  echo "== bootstrap chain: $$cc =="; \
	  $$cc $$f -o $(BUILDDIR)/zc-seed-$$tag bootstrap/zc.c $(call ZLINKOF,bootstrap/zc.c) -lm; \
	  $(BUILDDIR)/zc-seed-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b1-$$tag.c; \
	  $$cc $$f -o $(BUILDDIR)/zc-b1-$$tag $(BUILDDIR)/b1-$$tag.c $(call ZLINKOF,$(BUILDDIR)/b1-$$tag.c) -lm; \
	  $(BUILDDIR)/zc-b1-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b2-$$tag.c; \
	  $$cc $$f -o $(BUILDDIR)/zc-b2-$$tag $(BUILDDIR)/b2-$$tag.c $(call ZLINKOF,$(BUILDDIR)/b2-$$tag.c) -lm; \
	  $(BUILDDIR)/zc-b2-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b3-$$tag.c; \
	  if cmp -s $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c; then \
	    echo "  fixpoint OK (b2 == b3) under $$tag"; \
	  else \
	    echo "FAIL: seed-built compiler does not converge under $$tag"; \
	    diff $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c | head -6; exit 1; \
	  fi; \
	  $(BUILDDIR)/zc-b1-$$tag ztypes_smoke --src tests/unit --src src --system lib/system --emit-c $(BUILDDIR)/zt-$$tag.c; \
	  $$cc $$f -o $(BUILDDIR)/zt-$$tag $(BUILDDIR)/zt-$$tag.c $(call ZLINKOF,$(BUILDDIR)/zt-$$tag.c) -lm; \
	  $(BUILDDIR)/zt-$$tag | diff - tests/fixtures/ztypes_smoke_z/smoke.expected; \
	  echo "  correctness OK (seed-built zc compiles the ztypes smoke to golden) under $$tag"; \
	done
	@set -e; first=""; n=0; \
	for cc in $(BOOTSTRAP_CCS); do \
	  tag=$$(basename $$cc); n=$$(($$n + 1)); \
	  if [ -z "$$first" ]; then first=$$tag; else \
	    if ! cmp -s $(BUILDDIR)/b1-$$first.c $(BUILDDIR)/b1-$$tag.c; then \
	      echo "FAIL: the emitted C depends on which compiler built zc ($$first vs $$tag)"; \
	      diff $(BUILDDIR)/b1-$$first.c $(BUILDDIR)/b1-$$tag.c | head -6; exit 1; \
	    fi; \
	  fi; \
	done; \
	echo "agreement OK: $$n toolchain(s) emit byte-identical C"; \
	cp $(BUILDDIR)/b1-$$first.c $(BUILDDIR)/b1.c; \
	if cmp -s $(BUILDDIR)/b1.c bootstrap/zc.c; then \
	  echo "seed is current (b1 == committed seed)"; \
	else \
	  echo "note: seed has lagged (b1 != committed seed) -- run 'make bump-seed' when convenient"; \
	fi
	@echo "bootstrap seed OK: 'cc bootstrap/zc.c' builds a correct self-hosting zc (no Python)"

# install -- a self-contained tree at $(ROOT) + a $(BINDIR)/zc symlink.
install: bin/zc bin/zl bin/zls $(BUILDDIR)/tcc
	mkdir -p $(ROOT)/bin $(ROOT)/lib $(BINDIR)
	cp bin/zc $(ROOT)/bin/zc
	cp bin/zl $(ROOT)/bin/zl
	cp bin/zls $(ROOT)/bin/zls
	rm -rf $(ROOT)/lib/system $(ROOT)/lib/runtime $(ROOT)/lib/tcc $(ROOT)/docs $(ROOT)/src
	cp -r lib/system $(ROOT)/lib/system
	cp -r src/runtime $(ROOT)/lib/runtime
	cp $(BUILDDIR)/tcc $(ROOT)/bin/tcc
	cp -r $(TCCLIB) $(ROOT)/lib/tcc
	cp -r docs $(ROOT)/docs
	cp -r src $(ROOT)/src
	ln -sf $(ROOT)/bin/zc $(BINDIR)/zc
	ln -sf $(ROOT)/bin/zl $(BINDIR)/zl
	ln -sf $(ROOT)/bin/zls $(BINDIR)/zls
	@echo "installed zc, zl, zls -> $(BINDIR) (tree: $(ROOT), tcc: $(ROOT)/bin/tcc)"

# docs -- render the .pdoc documentation to HTML. Commit the regenerated .html.
# Needs the picodoc renderer at ../picodoc-c/picodoc (see docs/Makefile).
docs:
	$(MAKE) -C docs
	@echo "rendered docs/ -- commit the regenerated .html"

# WARN_CCS -- the compilers warn-check gates. Same reasoning as
# CI_BOOTSTRAP_CCS: a compiler named here but missing is an error, because a
# shorter list is a smaller claim.
WARN_CCS ?= $(CC) clang $(BUILDDIR)/tcc

# WARNSET_* -- the warnings each family actually IMPLEMENTS. QUALWERR's
# gcc/clang split, generalised: clang rejects gcc's -Werror=discarded-qualifiers
# outright, and tcc implements exactly six warnings (`tcc -hh`) and silently
# accepts every other -W it is handed. Passing tcc gcc's set would be the same
# defect as passing it -fsanitize=address -- the gate would report checks it had
# not run. -Wunsupported is tcc's own version of this rule, and is on
# deliberately: it makes tcc say when it is ignoring something.
WARNSET_gcc   := $(CFLAGS_BASE) -Werror=discarded-qualifiers $(OPTFLAGS)
WARNSET_clang := $(CFLAGS_BASE) -Werror=incompatible-pointer-types-discards-qualifiers $(OPTFLAGS)
WARNSET_tcc   := -B $(TCCLIB) -Wall -Wunsupported \
                 -Werror=implicit-function-declaration -Werror=discarded-qualifiers

# warn-check -- compile every emitted C file with each warning as an error, with
# every compiler that has to build it.
#
# All FOUR files, because they are not the same program: zl and zls carry units
# zc does not, so a dead store reachable only from one of them shows up only
# there -- and the committed SEED is what a fresh clone compiles first, so a
# warning left in it greets every new checkout no matter how clean src/ is.
#
# All THREE compilers, because they do not see the same things. tcc's "function
# might return no value" is the one diagnostic that points at a genuinely
# missing tail return, which surfaces at runtime as `zpanic: out of memory` and
# nowhere else; gcc and clang never report it. The residue is zero on all three.
warn-check: bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c $(BUILDDIR)/tcc
	@set -e; \
	for cc in $(WARN_CCS); do \
	  tag=$$(basename $$cc); \
	  case "$$tag" in \
	    *tcc*)   w='$(WARNSET_tcc)';; \
	    *clang*) w='$(WARNSET_clang)';; \
	    *)       w='$(WARNSET_gcc)';; \
	  esac; \
	  if ! command -v $$cc >/dev/null 2>&1 && [ ! -x $$cc ]; then \
	    echo "warn-check FAIL: '$$cc' is not available"; \
	    echo "  WARN_CCS names the compilers this gate speaks for; install it, or"; \
	    echo "  narrow the list deliberately: make warn-check WARN_CCS='gcc'"; \
	    exit 1; \
	  fi; \
	  for f in bootstrap/zc.c bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c; do \
	    $$cc $$w -Werror -c $$f -o /dev/null; \
	  done; \
	  echo "  $$tag: clean on seed + zc + zl + zls"; \
	done
	@echo "warn-check OK: zero warnings from $(words $(WARN_CCS)) compiler(s) on all four emitted files"

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
	@$(PERFCC) -std=c17 -w $(PERFOPT) -o $@ $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o bin/zc.c $(call ZLINKOF,bin/zc.c) -lpthread -lm

perf: $(PERFBIN)
	@echo "== zerolang line count (.z) =="
	@lsrc=$$(cat src/*.z | wc -l); llib=$$(cat lib/system/*.z lib/system/system/*.z | wc -l); \
	  printf "  src/*.z: %s    lib/system/**/*.z: %s    total: %s\n" "$$lsrc" "$$llib" "$$((lsrc + llib))"
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
# ALLOC_BASELINE -- heap blocks for one self-compile of $(PERFBIN) (gcc -O1, the
# default hash, --emit-c /dev/null): the count at the last commit that measured
# it. The number is bit-identical run to run, so it is a sound ratchet where wall
# and cycles are not. perf-strict fails ABOVE it; a commit that raises it states
# the reason in its message, and one that lowers the count lowers it here.
ALLOC_BASELINE := 2316743
# ALLOC_LINE -- the one measurement every allocation number comes from.
ALLOC_LINE = valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: //'

perf-strict: $(PERFBIN)
	@readelf -p .comment $(PERFBIN) | grep -qi clang \
	  && { echo "perf-strict: $(PERFBIN) is clang-built (PERFCC=$(PERFCC)) -- refusing to measure"; exit 1; } || true
	@sha=$$(git rev-parse --short HEAD); dirty=$$(git diff --quiet && git diff --cached --quiet && echo clean || echo DIRTY); \
	  echo "== perf-strict @ $$sha ($$dirty), $(PERFCC) $(firstword $(PERFOPT)) =="
	@$(PERFRUN) > /dev/null || { echo "perf-strict: self-compile FAILED (exit $$?)"; exit 1; }
	@line=$$($(ALLOC_LINE)); \
	  echo "  $$line"; \
	  a=$$(echo "$$line" | sed 's/ allocs.*//;s/,//g'); f=$$(echo "$$line" | sed 's/.* allocs, //;s/ frees.*//;s/,//g'); \
	  test "$$a" = "$$f" || { echo "perf-strict: allocs != frees -- incomplete or leaking run"; exit 1; }; \
	  if [ "$$a" -gt "$(ALLOC_BASELINE)" ]; then \
	    echo "perf-strict FAIL: $$a allocations > ALLOC_BASELINE $(ALLOC_BASELINE) -- lower the count, or raise the baseline with the reason in the commit"; exit 1; \
	  elif [ "$$a" -lt "$(ALLOC_BASELINE)" ]; then \
	    echo "perf-strict: $$a < ALLOC_BASELINE $(ALLOC_BASELINE) -- lower the baseline in the Makefile"; \
	  else echo "perf-strict OK: $$a allocations (baseline $(ALLOC_BASELINE))"; fi
	@if command -v perf >/dev/null 2>&1 && perf stat -e instructions true >/dev/null 2>&1; then \
	  perf stat -e instructions -r 3 $(PERFRUN) 2>&1 | grep -E 'instructions' | sed 's/^ */  /'; \
	else echo "  (perf stat unavailable -- instructions not measured)"; fi

# pre-push -- what a commit must pass before it leaves the machine: the fast
# gates plus the allocation ratchet. Not in ci: valgrind costs 15-25s and a
# shared runner cannot promise the perf binary a quiet core.
pre-push: check test perf-strict
	@echo "PRE-PUSH GREEN: check + test + perf-strict (allocations <= $(ALLOC_BASELINE))"

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
	@$(ELIDECC) -std=c17 -w $(PERFOPT) -o $(BUILDDIR)/zc-elide bin/zc.c $(call ZLINKOF,bin/zc.c) -lpthread -lm
	@$(ELIDECC) -std=c17 -w $(PERFOPT) $(NOBUILTIN) -o $(BUILDDIR)/zc-noelide bin/zc.c $(call ZLINKOF,bin/zc.c) -lpthread -lm
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
# matter, and no USER source may say it: a generic names anyval or AnyRef. Two
# stdlib files keep counted residuals:
#   system.z (5) -- `return` and `typedef`, whose parameter is never consulted
#     (probed: bounding them to anyval does not reject a reftype), plus
#     `Iterator` and `OptionView`, which still span both families.
#   collections.z (0) -- every container template names the family it takes.
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
	m=$$(grep -lE 'Any\.generic' tests/fixtures/lsp_cases/*.msgs 2>/dev/null | wc -l); \
	if [ "$$m" -gt 0 ]; then \
	  echo "any-guard FAIL: $$m lsp .msgs inline source(s) spell Any.generic"; \
	  grep -lE 'Any\.generic' tests/fixtures/lsp_cases/*.msgs; \
	  echo "  didOpen inline text overrides the workspace file -- migrate the .msgs too."; \
	  exit 1; \
	fi; \
	echo "any-guard OK: user source clean; system.z=$$s (<=5) collections.z=$$c (<=0); lsp .msgs clean"

# shadow-guard -- a C type resolved from a NAME can pick up a builtin's spelling
# for a user type that shadows it; the id-based forms re-check. The two sites in
# convBodyOf are the standing exception: it generates natives.tbl from a fixed
# list of builtin numeric names the compiler owns, with no program in hand and
# so no tid to re-check, and nothing a user writes can reach it.
shadow-guard:
	@n1=$$(grep -c 'cTypeOf name:' src/zemitterc.z); \
	n2=$$(grep -c 'cTypeForName symtab:' src/zemitterc.z); \
	n3=$$(grep -c 'isStdlibUnitName' src/zemitterc.z); \
	n4=$$(cat src/*.z | grep -c 'isStdlibUnitName'); \
	n5=$$(cat src/*.z | grep -c 'crossUnitDemand\|recordDemand\|isDemanded'); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "shadow-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "shadow-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "'cTypeOf name:'" "$$n1" 9; \
	chk "'cTypeForName symtab:'" "$$n2" 0; \
	chk "'isStdlibUnitName'" "$$n3" 0; \
	chk "'isStdlibUnitName (any src)'" "$$n4" 0; \
	chk "'crossUnitDemand / recordDemand / isDemanded'" "$$n5" 0; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A type's C type must come from its canonical id, never from its NAME, or a"; \
	  echo "  user type shadowing a builtin scalar emits the C scalar instead of its struct."; \
	  echo "  A site holding a tid asks scalarCTypeFor / cTypeForNameTid / typeRefC, all of"; \
	  echo "  which route through scalarTidIsBuiltin -- the DECLARATION (`is native`), not a"; \
	  echo "  list of unit names. Hence isStdlibUnitName is banned from this file outright."; \
	  echo "  The cTypeOf sites that remain have no tid to ask: convBodyOf GENERATES the"; \
	  echo "  natives.tbl conversion rows, two are scalar-NAME predicates rather than C-type"; \
	  echo "  lookups, and the rest start from a typedef base or an AST name. If you have a"; \
	  echo "  tid, you are not one of them."; \
	  echo "  A reference demands the DECLARATION the environment walk finds for it, never a"; \
	  echo "  bare name: there is no unit-name list and no name-keyed demand set left to"; \
	  echo "  consult, and none may come back."; \
	  echo "  (If a site was legitimately removed, lower the baseline here instead.)"; \
	  exit 1; \
	fi; \
	echo "shadow-guard OK: cTypeOf name:=$$n1 (<=9)  cTypeForName symtab:=$$n2 (<=0)  isStdlibUnitName=$$n3/$$n4 (<=0)  demand set=$$n5 (<=0)"

# emitter-guard -- ratchet against name-resolution creep in the C emitter: the
# emitter reads typechecker stamps and canonical ids, and every remaining
# by-name resolution is a counted residual (template re-emission, probe-chain
# legs). A rising count means a new name-resolved site -- resolve from
# stamps/ids instead, or lower the baseline when a residual is legitimately
# removed. The sanctioned name lookups go through scalarCTypeFor /
# cTypeForNameTid, which re-check the tid for a user shadow. The last two
# counts pin where C names are BUILT: the type checker composes none, and the
# emitter spells the z_t{id} shape only inside its one composer, which the
# per-program table in emitC calls once per type.
emitter-guard:
	@e1=$$(grep -c 'ztypecheck.resolvedByKey' src/zemitterc.z); \
	e2=$$(grep -c 'ztypecheck.walkLookupTyperef' src/zemitterc.z); \
	e3=$$(grep -c 'resolveTypeIdByName' src/zemitterc.z); \
	e4=$$(grep -c 'userFnId' src/zemitterc.z); \
	e5=$$(grep -c 'childOwnershipText' src/zemitterc.z); \
	e6=$$(grep -c 'regNameOf' src/zemitterc.z); \
	e7=$$(grep -c 'ztypes.mangleVarName' src/zemitterc.z); \
	e8=$$(grep -cF 'io.readText' src/zemitterc.z); \
	e9=$$(grep -c 'monoOriginName' src/zemitterc.z); \
	e10=$$(grep -c 'ztypes.mangleMemberPrefix' src/zemitterc.z); \
	g1=$$(grep -c 'composeCname' src/ztypes.z); \
	g2=$$(grep -cF 'z_t\{' src/zemitterc.z); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "emitter-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "emitter-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "composeCname in src/ztypes.z" "$$g1" 0; \
	chk "'z_t{' literals in src/zemitterc.z" "$$g2" 3; \
	chk "ztypecheck.resolvedByKey" "$$e1" 0; \
	chk "ztypecheck.walkLookupTyperef" "$$e2" 5; \
	chk "resolveTypeIdByName" "$$e3" 21; \
	chk "userFnId" "$$e4" 31; \
	chk "childOwnershipText" "$$e5" 0; \
	chk "regNameOf" "$$e6" 96; \
	chk "ztypes.mangleVarName (both inside varCName)" "$$e7" 2; \
	chk "io.readText" "$$e8" 3; \
	chk "monoOriginName" "$$e9" 7; \
	chk "ztypes.mangleMemberPrefix (inside memberCPrefix)" "$$e10" 1; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  ctxCname instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: resolvedByKey=$$e1 walkLookup=$$e2 resolveByName=$$e3 userFnId=$$e4 ownText=$$e5 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 monoOrigin=$$e9 mangleMember=$$e10"

# deadcode-guard -- emitted statements that no path can reach. clang's
# -Wunreachable-code family is the oracle; gcc accepts the flag but never warns.
# Every example and corpus program is emitted and counted, so a new dead-code
# shape anywhere raises the number, not just one in the dedicated fixture
# (tests/fixtures/emitc_corpus/deadcode_shapes.z). The baseline is ZERO and a
# rise is a regression, not a number to bump. The two ways it happens: a site
# appended a statement after a block that had already diverged (ask
# blockDiverges in src/zemitterc.z about the block you just emitted -- never
# read a flag left over from someone else's block), or a constant-condition
# branch was emitted that docs/spec.pdoc promises not to emit (fold it; the
# typechecker stamps the winning clause). Divergence is the typechecker's
# `never` stamp and nothing else.
#
# user-native-guard -- a unit OUTSIDE src/runtime, shipping its own natives.tbl
# row and its own fragment, compiles AND LINKS AND RUNS (a fragment a hardcoded
# per-unit loader would miss emits its call correctly and fails at link with an
# implicit declaration). The runtime dir is BUILT here rather than committed -- src/runtime
# plus the fixture's one row and one fragment -- so it cannot drift from the real
# one, and the guard fails if the fragment stops loading.
user-native-guard: bin/zc
	@d=$$(mktemp -d); fail=0; \
	mkdir -p $$d/rt; cp -r src/runtime/. $$d/rt/; \
	cat tests/fixtures/user_native/mystery.tbl >> $$d/rt/natives.tbl; \
	cp tests/fixtures/user_native/_Z_MYSTERY_CONJURE.inc $$d/rt/natives/; \
	bin/zc mystery --src tests/fixtures/user_native --system lib/system \
	  --runtime $$d/rt --emit-c $$d/mystery.c > $$d/emit.log 2>&1 \
	  || { echo "user-native-guard FAIL: emit"; sed -n 1,5p $$d/emit.log; fail=1; }; \
	if [ $$fail -eq 0 ]; then \
	  $(CC) $(CFLAGS) -o $$d/mystery $$d/mystery.c $(call ZLINKOF,$$d/mystery.c) -lm > $$d/cc.log 2>&1 \
	    || { echo "user-native-guard FAIL: the unit's fragment did not load (link)"; \
	         grep -m1 error $$d/cc.log; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  got=$$($$d/mystery); \
	  if [ "$$got" != "42" ]; then \
	    echo "user-native-guard FAIL: ran but printed '$$got', want 42"; fail=1; fi; \
	fi; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "user-native-guard OK: a unit outside src/runtime links and runs its own native"

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

# require-guard -- zlink-guard's other half. That one pins which programs a
# `require:` block CONTRIBUTES a library to; this pins which programs it is
# allowed to REJECT. Both read the same rule -- a block speaks only for a
# program that reaches its unit -- and only a toolchain the block objects to
# exercises this side, so it is measured under `--cc tcc`: quadfloat's block
# reports E0601 there. A rise means a unit now rejects programs that never
# touch it (which is what made `--cc tcc` reject the entire corpus); a fall
# means a guard stopped firing for a program that does touch it.
REQUIRE_TCC_BASELINE := 5

require-guard: bin/zc
	@n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  if bin/zc emit $$f --system lib/system --cc tcc -o /dev/null 2>&1 | grep -q 'error\[E0601\]'; then \
	    n=$$(($$n + 1)); rep="$$rep  $$b\n"; \
	  fi; \
	done; \
	if [ "$$n" -ne $(REQUIRE_TCC_BASELINE) ]; then \
	  echo "require-guard FAIL: $$n program(s) rejected under --cc tcc (baseline $(REQUIRE_TCC_BASELINE))"; \
	  printf "$$rep"; \
	  echo "  a require: block speaks only for a program that REACHES its unit."; \
	  exit 1; \
	fi; \
	echo "require-guard OK: $$n programs rejected under --cc tcc (baseline $(REQUIRE_TCC_BASELINE))"

# mode-parity -- `--cc-mode inproc` must be a faster way to reach the same
# program, not a different backend. It compares program OUTPUT and exit code,
# never the binaries: the two link paths legitimately produce different bytes
# (libtcc links through the state that compiled; the driver re-reads the
# object), and diffing those would fail for no reason anyone could act on.
# The run cases are the sample -- they are the ones with a golden to be wrong
# against. The programs tcc rejects are compared too: a REFUSAL has to match
# in both modes as much as a result does, which is what proves the in-process
# error callback reports what the driver prints. Only the pid in the temp path
# zc names is normalised away; it differs between two runs of the same mode.
# os_platform is the one exclusion, and it is not an exception to the rule: it
# PRINTS platform.ccmode, so its output is supposed to differ. A program that
# reports which mode built it is the one program that cannot be mode-invariant.
#
# --ldflags is the one flag whose meaning is deliberately NOT mode-invariant,
# so it cannot be a parity row: spawn hands it to a linker and inproc has no
# linker to hand it to, and zc refuses rather than dropping it. The stanza at
# the end asserts that asymmetry directly -- both halves, so a regression in
# either direction reds this gate. Without it the loop above passes --ldflags
# nowhere and the whole class is invisible to CI.
mode-parity: bin/zc $(BUILDDIR)/tcc
	@mkdir -p $(BUILDDIR)/parity; n=0; bad=0; \
	while read -r name dir rest; do \
	  [ -n "$$name" ] || continue; \
	  [ "$$name" = os_platform ] && continue; \
	  args=$(BUILDDIR)/parity/$$name.args; \
	  : > $$args; \
	  [ -f tests/fixtures/run_golden/$$name.args ] && cp tests/fixtures/run_golden/$$name.args $$args; \
	  for mode in spawn inproc; do \
	    bin/zc build $$name --src $$dir --system lib/system --cc tcc \
	      --cc-mode $$mode -o $(BUILDDIR)/parity/$$name.$$mode \
	      > $(BUILDDIR)/parity/$$name.$$mode.build 2>&1 || true; \
	    if [ -x $(BUILDDIR)/parity/$$name.$$mode ]; then \
	      ( cd $(BUILDDIR)/parity && xargs -a $$name.args ./$$name.$$mode ) \
	        > $(BUILDDIR)/parity/$$name.$$mode.out 2>&1; \
	      echo "exit=$$?" >> $(BUILDDIR)/parity/$$name.$$mode.out; \
	    else \
	      cp $(BUILDDIR)/parity/$$name.$$mode.build $(BUILDDIR)/parity/$$name.$$mode.out; \
	    fi; \
	  done; \
	  n=$$(($$n + 1)); \
	  for mode in spawn inproc; do \
	    sed -i -e 's|/zc-[0-9][0-9]*-|/zc-PID-|g' $(BUILDDIR)/parity/$$name.$$mode.out; \
	  done; \
	  if ! cmp -s $(BUILDDIR)/parity/$$name.spawn.out $(BUILDDIR)/parity/$$name.inproc.out; then \
	    echo "mode-parity FAIL: $$name differs between spawn and inproc"; \
	    diff $(BUILDDIR)/parity/$$name.spawn.out $(BUILDDIR)/parity/$$name.inproc.out | head -6; \
	    bad=1; \
	  fi; \
	done < tests/fixtures/run_cases.txt; \
	if [ $$bad -ne 0 ]; then \
	  echo "  in-process compilation must produce the same PROGRAM, not the same bytes."; \
	  exit 1; \
	fi; \
	echo "mode-parity OK: $$n run cases identical under --cc-mode spawn and inproc (output and exit code, not bytes)"
	@p=$(BUILDDIR)/parity; rm -f $$p/ldf.spawn $$p/ldf.inproc; \
	bin/zc build hello --src examples --system lib/system --cc tcc --cc-mode spawn \
	  --ldflags "-Wl,--as-needed" -o $$p/ldf.spawn > $$p/ldf.spawn.log 2>&1; sp=$$?; \
	bin/zc build hello --src examples --system lib/system --cc tcc --cc-mode inproc \
	  --ldflags "-Wl,--as-needed" -o $$p/ldf.inproc > $$p/ldf.inproc.log 2>&1; ip=$$?; \
	if [ $$sp -ne 0 ] || [ ! -x $$p/ldf.spawn ] || [ "$$($$p/ldf.spawn)" != "Hello, World!" ]; then \
	  echo "mode-parity FAIL: --ldflags did not reach the linker under spawn (exit $$sp)"; \
	  cat $$p/ldf.spawn.log; exit 1; \
	fi; \
	if [ $$ip -ne 2 ] || [ -e $$p/ldf.inproc ]; then \
	  echo "mode-parity FAIL: --ldflags under inproc must be refused with exit 2, got $$ip"; \
	  cat $$p/ldf.inproc.log; exit 1; \
	fi; \
	if ! grep -q -- '--ldflags' $$p/ldf.inproc.log || ! grep -q inproc $$p/ldf.inproc.log; then \
	  echo "mode-parity FAIL: the inproc --ldflags refusal must name the flag and the mode"; \
	  cat $$p/ldf.inproc.log; exit 1; \
	fi; \
	echo "mode-parity OK: --ldflags honoured under spawn, refused under inproc (a deliberate asymmetry, not a parity row)"

# zlink-rules-guard -- every rule that LINKS a zerolang-emitted C file takes its
# -l set from that file's own `zlink:` header, rather than hardcoding one.
#
# The seed rules did not. bootstrap/zc.c declares `dl` and makes two dlopen
# calls, and the rules that build it asked for -lm alone -- latent only because
# glibc >= 2.34 folded dlopen into libc, and load-bearing below that and on
# musl. Nothing noticed for as long as the rule existed, because a missing -l
# that the libc happens to supply looks exactly like a correct build.
#
# A grep, because a grep is the thing that would have caught it: a recipe line
# that names a .c and passes an -l is linking, and it must go through ZLINKOF.
# Compile-only lines carry -c and are not linking; `zc emit -o foo.c` lines
# pass no -l. Both fall out without an exemption list.
zlink-rules-guard:
	@off=$$(grep -n -- '-l' Makefile | grep -v '^[0-9]*:#' | grep -vE ' -c ' \
	         | grep -E '\.c\b' | grep -v ZLINKOF); \
	if [ -n "$$off" ]; then \
	  echo "zlink-rules-guard FAIL: these rules link emitted C with a hardcoded -l set:"; \
	  echo "$$off"; \
	  echo "  take the libraries from the artifact: \$$(call ZLINKOF,<the .c>)"; \
	  exit 1; \
	fi; \
	echo "zlink-rules-guard OK: every rule linking emitted C reads its -l set from the artifact"


# refusal-guard -- zc says so and exits non-zero when it cannot honour what it
# was asked for. Each pair below pins a REFUSAL together with the DELIVERY that
# makes the refusal meaningful: a flag zc rejects in one mode has to be doing
# something in the other, or the refusal is a missing feature wearing an error
# message. Both halves, so a regression in either direction reds this gate.
#
# These cannot be errors/ fixtures. That runner's command always ends in
# --emit-c (zcArgv, src/ztestrunner.z), so it never enters the compile/link
# branch where the mode and the compiler are resolved and these refusals live.
#
# The last case is the other half of the same rule: a value-taking flag that
# ends the command line is a usage error. It used to read one past argv and
# abort in the allocator's index check, which names no flag and exits 1.
refusal-guard: bin/zc $(BUILDDIR)/tcc
	@d=$(BUILDDIR)/refusal; rm -rf $$d; mkdir -p $$d; bad=0; \
	bin/zc build hello --src examples --system lib/system --cc tcc --cc-mode spawn \
	  --ldflags "-Wl,--nonsense-flag" -o $$d/a > $$d/a.log 2>&1; rc=$$?; \
	if [ $$rc -eq 0 ] || [ -e $$d/a ]; then \
	  echo "refusal-guard FAIL: --ldflags is not reaching the linker under spawn (exit $$rc)"; \
	  cat $$d/a.log; bad=1; \
	fi; \
	bin/zc build hello --src examples --system lib/system --cc tcc --cc-mode inproc \
	  --ldflags "-Wl,--nonsense-flag" -o $$d/b > $$d/b.log 2>&1; rc=$$?; \
	if [ $$rc -ne 2 ] || [ -e $$d/b ]; then \
	  echo "refusal-guard FAIL: --ldflags under --cc-mode inproc must exit 2, got $$rc"; \
	  cat $$d/b.log; bad=1; \
	elif ! grep -q -- '--ldflags' $$d/b.log || ! grep -q inproc $$d/b.log; then \
	  echo "refusal-guard FAIL: the --ldflags refusal must name the flag and the resolved mode"; \
	  cat $$d/b.log; bad=1; \
	fi; \
	bin/zc build os_platform --src examples --system lib/system --cc gcc \
	  --target sparc-solaris-nonsense -o $$d/c > $$d/c.log 2>&1; rc=$$?; \
	if [ $$rc -ne 2 ] || ! grep -q "names no operating system" $$d/c.log; then \
	  echo "refusal-guard FAIL: an unrecognised --target must exit 2, got $$rc"; \
	  cat $$d/c.log; bad=1; \
	elif ! grep -q "operating systems: linux" $$d/c.log; then \
	  echo "refusal-guard FAIL: the --target refusal must list the vocabulary it accepts"; \
	  cat $$d/c.log; bad=1; \
	fi; \
	bin/zc build os_platform --src examples --system lib/system --cc tcc \
	  --target x86_64-linux-windows -o $$d/e > $$d/e.log 2>&1; rc=$$?; \
	if [ $$rc -ne 2 ] || ! grep -q "more than one operating system" $$d/e.log; then \
	  echo "refusal-guard FAIL: an ambiguous --target must exit 2, not resolve to whichever test ran last (got $$rc)"; \
	  cat $$d/e.log; bad=1; \
	fi; \
	bin/zc build os_platform --src examples --system lib/system --cc tcc \
	  --target aarch64-linux -o $$d/f > $$d/f.log 2>&1; rc=$$?; \
	if [ $$rc -ne 2 ] || [ -e $$d/f ]; then \
	  echo "refusal-guard FAIL: --cc tcc with a non-host --target must exit 2, got $$rc"; \
	  cat $$d/f.log; bad=1; \
	elif ! grep -q "host-only linux-x86_64" $$d/f.log; then \
	  echo "refusal-guard FAIL: the tcc cross refusal must say why tcc cannot"; \
	  cat $$d/f.log; bad=1; \
	fi; \
	if ! bin/zc env --target x86_64-w64-mingw32 | grep -q '^ZC_CC=x86_64-w64-mingw32-gcc$$'; then \
	  echo "refusal-guard FAIL: the documented <triple>-gcc cross path stopped resolving"; \
	  bin/zc env --target x86_64-w64-mingw32; bad=1; \
	fi; \
	bin/zc build os_platform --src examples --system lib/system --cc gcc \
	  --target x86_64-linux -o $$d/g > $$d/g.log 2>&1; rc=$$?; \
	if [ $$rc -ne 0 ] || [ "$$($$d/g | head -1)" != "platform=linux" ]; then \
	  echo "refusal-guard FAIL: a HOST --target must still build and fold to the host (exit $$rc)"; \
	  cat $$d/g.log; bad=1; \
	fi; \
	for fl in --src --system --runtime --emit-c --dump-sql -o --cc --cc-mode --tcc-lib --target --cflags --ldflags; do \
	  bin/zc build hello --src examples --system lib/system $$fl > $$d/m.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 2 ] || ! grep -q -- "zc: $$fl needs a value" $$d/m.log; then \
	    echo "refusal-guard FAIL: a trailing $$fl must exit 2 naming the flag, got $$rc"; \
	    cat $$d/m.log; bad=1; \
	  fi; \
	done; \
	if [ $$bad -ne 0 ]; then \
	  echo "  zc never accepts a flag it will ignore: it names what it resolved and exits 2."; \
	  exit 1; \
	fi; \
	echo "refusal-guard OK: every unhonourable flag combination is refused, and its honoured counterpart still works"


# static-tcc-guard -- the vendored tinycc is LGPL-2.1 inside a dual MIT/Apache
# tree, so it may be reached by dlopen and by nothing else: linking it, static
# or dynamic, is what makes zc a combined work and triggers LGPL section 6's
# relink obligation. dlopen names its entry points as STRINGS, so a compliant
# zc has no tcc symbol at all -- a static link leaves `T tcc_new`, a `-ltcc`
# link leaves `U tcc_new`, and this catches both. The leading space keeps our
# own natives (z_tcc_compileToExe and friends) from matching. A licence posture
# that lives only in a comment rots; this one is mechanised.
static-tcc-guard: bin/zc bin/zl bin/zls
	@fail=0; \
	for b in bin/zc bin/zl bin/zls; do \
	  if nm -A $$b 2>/dev/null | grep -qE ' tcc_(new|delete|set_lib_path|output_file|add_file)$$'; then \
	    echo "static-tcc-guard FAIL: $$b names a libtcc symbol -- it must be dlopen'd, never linked"; \
	    fail=1; \
	  fi; \
	done; \
	if [ $$fail -ne 0 ]; then \
	  echo "  vendored tinycc is LGPL-2.1 in a dual MIT/Apache tree: dynamic LOADING only."; \
	  echo "  See vendor/tinycc/VERSION.md and the Third-party table in README.md."; \
	  exit 1; \
	fi; \
	echo "static-tcc-guard OK: no libtcc symbols in the driver binaries"

# zlink-guard -- a `require:` block earns its keep only if it applies to the
# programs that reach the declaring unit and to no others. Nothing else can
# catch a mistake here: gcc always has libquadmath, so a requiredLibs that
# returned "quadmath" unconditionally would link fine and pass every other
# gate. A rise means something now reaches a unit it did not; a fall means a
# program lost a need it had.
ZLINK_BASELINE := 3

zlink-guard: bin/zc
	@n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  l=$$(bin/zc emit $$f --system lib/system 2>/dev/null \
	        | $(ZLINKSED)); \
	  if [ -n "$$l" ]; then n=$$(($$n + 1)); rep="$$rep  $$b: $$l\n"; fi; \
	done; \
	if [ "$$n" -ne $(ZLINK_BASELINE) ]; then \
	  echo "zlink-guard FAIL: $$n program(s) declare a link library (baseline $(ZLINK_BASELINE))"; \
	  printf "$$rep"; \
	  exit 1; \
	fi; \
	echo "zlink-guard OK: $$n programs declare a link library (baseline $(ZLINK_BASELINE))"

# emit-set / ident-set -- the byte-identity oracle for compiler refactors.
# `make emit-set OUT=dir [ZC=bin/zc]` emits every example, every corpus program
# and the three drivers (zc, zl, zls) as C into OUT. `make ident-set A=dir B=dir`
# normalises every renumbering id form (z_t{N}, _t{N}_ mid-symbol, Z_T{N}_,
# _i{N}, _zs{N}, z_v{N}) in both trees and diffs them; what remains is a
# semantic difference to explain, or a bug. A partial normaliser reports
# phantom content changes -- widen it before concluding a diff is semantic.
EMITSET_ZC ?= bin/zc
emit-set:
	@test -n "$(OUT)" || { echo "usage: make emit-set OUT=dir [ZC=bin/zc]"; exit 1; }; \
	zc="$${ZC:-$(EMITSET_ZC)}"; mkdir -p "$(OUT)"; n=0; bad=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  grep -q '^main:' $$f || continue; \
	  if $$zc emit $$f -o "$(OUT)/$$b.c" >"$(OUT)/$$b.log" 2>&1; then n=$$(($$n + 1)); rm -f "$(OUT)/$$b.log"; \
	  else bad="$$bad $$b"; rm -f "$(OUT)/$$b.c"; fi; \
	done; \
	for d in zc zl zls; do \
	  if $$zc $$d --src src --system lib/system $(ZCHASH) --emit-c "$(OUT)/$$d.c" >"$(OUT)/$$d.log" 2>&1; then n=$$(($$n + 1)); rm -f "$(OUT)/$$d.log"; \
	  else bad="$$bad $$d"; fi; \
	done; \
	echo "emit-set: $$n programs emitted into $(OUT)"; \
	if [ -n "$$bad" ]; then echo "emit-set: NOT emitted (see .log):$$bad"; fi

ident-set:
	@test -n "$(A)" && test -n "$(B)" || { echo "usage: make ident-set A=dir B=dir"; exit 1; }; \
	na=$$(mktemp -d); nb=$$(mktemp -d); rep=$$(mktemp); \
	norm() { sed -E -e 's/z_t[0-9]+/z_tN/g' -e 's/_t[0-9]+_/_tN_/g' -e 's/Z_T[0-9]+_/Z_TN_/g' \
	  -e 's/_i[0-9]+\b/_iN/g' -e 's/_zs[0-9]+\b/_zsN/g' -e 's/z_v[0-9]+\b/z_vN/g' "$$1" > "$$2"; }; \
	for f in "$(A)"/*.c; do b=$$(basename $$f); norm "$$f" "$$na/$$b"; done; \
	for f in "$(B)"/*.c; do b=$$(basename $$f); norm "$$f" "$$nb/$$b"; done; \
	raw=$$(diff -rq "$(A)" "$(B)" | grep -c '\.c' || true); \
	if diff -rq "$$na" "$$nb" > "$$rep"; then \
	  echo "ident-set OK: identical after normalisation ($$raw file(s) differ raw)"; rm -rf "$$na" "$$nb" "$$rep"; \
	else \
	  echo "ident-set: $$(grep -c . "$$rep") file(s) differ after normalisation ($$raw raw):"; \
	  sed 's/^/  /' "$$rep" | head -60; rm -rf "$$na" "$$nb" "$$rep"; exit 1; \
	fi

# const-row-guard -- a natives.tbl row carrying `const=<positions>` answers only
# when those ARGUMENT positions are compile-time constants; the ordinary row
# answers otherwise. Both directions are checked, because either failure is
# silent. A variant that stops being selected costs nothing visible -- the
# guarded form is still correct, just branchier, and no golden would move. A
# variant selected for a RUNTIME operand is worse and just as quiet: the
# variant names its hole more than once, and holes fill VERBATIM, so a call on
# the right would be evaluated once per mention. Binding the operand into `_l`
# / `_r` is what the ordinary rows do to prevent exactly that, so the presence
# of an `_r` binding is what tells the two forms apart in the emitted C.
#
# const_shift_form is the sample: six constant counts, which must bind no `_r`,
# and three that are not constant -- two locals and a CALL -- which must each
# bind one. The fixture answers for the values; this answers for the form.
CONST_ROW_VARIABLE := 3

const-row-guard: bin/zc
	@d=$$(mktemp -d); em=$$d/emitted.c; \
	bin/zc emit tests/fixtures/emitc_corpus/const_shift_form.z -o $$em || { \
	  echo "const-row-guard FAIL: const_shift_form did not emit"; rm -rf $$d; exit 1; }; \
	n=$$(grep -o '_r = ' $$em | wc -l); rm -rf $$d; \
	if [ "$$n" -ne $(CONST_ROW_VARIABLE) ]; then \
	  echo "const-row-guard FAIL: $$n operand bindings, expected $(CONST_ROW_VARIABLE)"; \
	  echo "  More means a constant count stopped selecting the const= row."; \
	  echo "  Fewer means a NON-constant one selected it, and the variant names"; \
	  echo "  its hole more than once -- a call operand would be evaluated twice."; \
	  exit 1; \
	fi; \
	echo "const-row-guard OK: $$n non-constant shift operands bound, the rest fold"

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
#
# IT COMPILES WHAT IT EMITS. It used to write the C to /dev/null and judge by
# zc's exit status, which is not the same question: zc emitted happily for
# every program in the corpus while the C it produced did not compile for ANY
# of them -- `Any` and `AnyRef` are arm-less generic bounds, and an empty C
# enum is invalid. A gate that never reads its own output cannot see that, and
# this one printed "clean under --eager" for as long as it existed.
#
# Judged on its own, never against a non-eager run: `--eager` does not require
# a `main` (it is not asking what a root reaches), so a library fixture emits
# here and is E0039 without it. The two sides are not comparable and the
# difference is not the signal.
# alias-label-guard -- one type, ONE label. A mono's label head is its
# TEMPLATE'S DECLARED name, never the alias a use site reached it by.
# lib/system/core.z binds collections.ListRef under both `List` and `ListRef`
# (likewise Map/MapRR and Set/SetRef), and a user may alias anything; while the
# head came from the source text, whichever mint site ran first decided the
# spelling. That is what made the two modes disagree and broke the hardcoded
# tag in src/runtime/natives/_Z_IO_LIST_DIR.inc under --eager. Composing the
# head at the mint funnel (getOrMintSpec) took this corpus from 7811
# alias-headed labels to 0.
#
# A program that DECLARES its own List/Map/Set -- usershadow.z does -- keeps
# that head legitimately, because then it IS the declared name. The check reads
# each program's source for such a declaration rather than carrying a skip
# list, so a new shadow fixture needs no edit here and cannot silently weaken
# it.
alias-label-guard: bin/zc
	@d=$$(mktemp -d); bad=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  if grep -qE '^(List|Map|Set): *(class|record|union|variant|protocol|facet)' $$f; then continue; fi; \
	  b=$$(basename $$f .z); \
	  for m in "" "--eager"; do \
	    bin/zc emit $$f $$m --readable-names -o $$d/x.c >/dev/null 2>&1 || continue; \
	    if grep -qE 'z_t[0-9]+_(List|Map|Set)_' $$d/x.c; then bad="$$bad $$b$$m"; fi; \
	  done; \
	done; \
	rm -rf $$d; \
	if [ -n "$$bad" ]; then \
	  echo "alias-label-guard FAIL: a mono is labelled with an ALIAS head:$$bad"; \
	  echo "  The label head must be the template's DECLARED name. A caller that"; \
	  echo "  composes its own head reintroduces the path-dependence -- pass only"; \
	  echo "  the argument suffix to getOrMintSpec."; \
	  exit 1; \
	fi; \
	echo "alias-label-guard OK: no mono carries an alias head (both modes, examples + corpus)"

# fwd-shape-guard -- a struct type is emitted in ONE of two shapes, and which
# one is decided by whether something forward-declared it first: a tagged body
# `struct X_t { ... };` when it did, an ANONYMOUS `typedef struct { ... } X_t;`
# when it did not. A forward declaration written AFTER an anonymous body names
# a struct tag that does not exist, so the two must never both happen to one
# type.
#
# They did, for a user generic class or record mono passed to a function:
# emitMainFwdDecls forward-declares a mono only when it needs a destructor or
# is a container, so `Slot i64` took the anonymous shape -- and ensureFwdStruct
# then forward-declared it for the function-pointer typedef, having asked only
# whether a forward existed and never whether the BODY was already out. zc
# emitted that C and exited 0; the C compiler rejected it.
#
# The scan pairs each `typedef struct {` with the FIRST following line that
# starts with `}`. Resetting on any such line is the load-bearing part: a state
# machine that stays open past its own block mis-reads the next TAGGED block's
# closing line and invents collisions that are not there.
fwd-shape-guard: bin/zc
	@d=$$(mktemp -d); bad=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  for m in "" "--eager"; do \
	    bin/zc emit $$f $$m -o $$d/x.c >/dev/null 2>&1 || continue; \
	    awk '/^typedef struct \{$$/ { inb=1; next } \
	         inb == 1 && substr($$0,1,1) == "}" { \
	           inb=0; \
	           if ($$0 ~ /^\} z_t[0-9]+_t;$$/) { s=$$0; sub(/^\} /,"",s); sub(/;$$/,"",s); print s } \
	           next }' $$d/x.c | LC_ALL=C sort -u > $$d/untagged; \
	    grep -oE '^typedef struct z_t[0-9]+_t z_t[0-9]+_t;' $$d/x.c 2>/dev/null \
	      | sed -e 's/^typedef struct //' -e 's/ .*//' | LC_ALL=C sort -u > $$d/fwd; \
	    both=$$(LC_ALL=C comm -12 $$d/untagged $$d/fwd); \
	    if [ -n "$$both" ]; then bad="$$bad $$b$$m"; fi; \
	  done; \
	done; \
	rm -rf $$d; \
	if [ -n "$$bad" ]; then \
	  echo "fwd-shape-guard FAIL: a type is emitted UNTAGGED and forward-declared:$$bad"; \
	  echo "  An anonymous 'typedef struct { ... } X_t;' has no tag, so a later"; \
	  echo "  'typedef struct X_t X_t;' names nothing. Whoever writes the forward"; \
	  echo "  must first ask whether the body is already out (typeStructEmitted)."; \
	  exit 1; \
	fi; \
	echo "fwd-shape-guard OK: no type is both emitted untagged and forward-declared (both modes, examples + corpus)"

# EAGER_KNOWN -- the programs still bad under `--eager`. Empty, and a name
# added here needs the cause written beside it.
#
# Movement in EITHER direction fails: a new name means a regression, a lost one
# means it was fixed and the row must go in the same commit.
EAGER_KNOWN :=

eager-guard: bin/zc
	@d=$$(mktemp -d); bad=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); em=$$d/$$b.c; \
	  if ! bin/zc emit $$f --eager -o $$em >/dev/null 2>&1; then \
	    bad="$$bad $$b"; continue; \
	  fi; \
	  if ! $(CC) -std=c17 -w -Werror=implicit-function-declaration \
	       -fsyntax-only $$em >/dev/null 2>&1; then \
	    bad="$$bad $$b"; \
	  fi; \
	done; \
	rm -rf $$d; \
	new=""; for b in $$bad; do \
	  case " $(EAGER_KNOWN) " in *" $$b "*) ;; *) new="$$new $$b";; esac; \
	done; \
	gone=""; for k in $(EAGER_KNOWN); do \
	  case " $$bad " in *" $$k "*) ;; *) gone="$$gone $$k";; esac; \
	done; \
	if [ -n "$$new" ]; then \
	  echo "eager-guard FAIL: bad under --eager and not known:$$new"; \
	  echo "  A definition nothing references reached the emitter and it wrote"; \
	  echo "  C that does not compile. Fix it, do not add it to EAGER_KNOWN."; \
	  exit 1; \
	fi; \
	if [ -n "$$gone" ]; then \
	  echo "eager-guard FAIL: known-bad now clean:$$gone"; \
	  echo "  Delete the row from EAGER_KNOWN in the same commit that fixed it."; \
	  exit 1; \
	fi; \
	echo "eager-guard OK: examples + corpus emit AND compile under --eager ($(words $(EAGER_KNOWN)) known)"

# member-guard -- ratchet against declaration-bypassing member special-cases in
# the type checker. TWO spellings carry them and the guard counts both:
# `cn.stringView == "..."` where the name is an owned String, and `cn == "..."`
# where it is already a view -- 24 in all. The `.drop` marker used to add four
# of the second kind; they now route through isDropMarker, so its spelling
# lives in one place.
#
# THE OWNERSHIP VOCABULARY IS NO LONGER HERE. lock / borrow / take / view /
# hold are interned as well-known ids (zast.names) and compared as ids
# in pathOwnership, so this guard no longer catches a new marker word. What
# catches one instead is the type system: zparamownership is matched
# EXHAUSTIVELY in four places, so a new arm is a compile error rather than a
# silent miss.
#
# The sanctioned markers are, exhaustively, what the baseline still counts:
# access (private / public), the conversions (copy / str), the container
# markers (tag / array / index), the definition keywords (typedef / return /
# create / error / panic), and the `Iterator` protocol marker. A rising count means a new hardcoded
# string-keyed special-case -- resolve members through their declared childOf
# edges instead (the system units are the source of truth). Bump the baseline
# here only for a genuinely-sanctioned marker.
#
# TWICE now this ratchet has been loose with nobody noticing. It was 37 against
# an actual 18. Then `3fae8cdb lib+src: the view conversions are camelCase`
# renamed .stringview to .stringView, the pattern here still spelled it
# lowercase, and from that commit until this one it matched NOTHING -- counting
# zero against a baseline of 18 and printing OK every run, because the
# under-baseline branch only advises. A guard that names a spelling dies when
# the spelling moves, and it dies silently.
member-guard:
	@m1=$$(grep -cE '[a-z]*cn\.stringView ==|[a-z]*cn == "' src/ztypecheck.z); \
	if [ "$$m1" -gt 22 ]; then \
	  echo "member-guard FAIL: string-keyed member compares = $$m1 (baseline 22)"; \
	  echo "  A new hardcoded string-keyed member/marker special-case was added to the"; \
	  echo "  type checker. Resolve members through their declared childOf edges (the"; \
	  echo "  system units are the source of truth); bump the baseline only for a"; \
	  echo "  genuinely-sanctioned marker."; \
	  exit 1; \
	fi; \
	if [ "$$m1" -lt 22 ]; then \
	  echo "member-guard: string-keyed member compares = $$m1 < baseline 22 -- lower the baseline here"; \
	fi; \
	echo "member-guard OK: string-keyed member compares = $$m1 (<=22)"

# highlight-guard -- the two syntax highlighters must carry the language's
# actual vocabulary. THE LANGUAGE IS THE SOURCE OF TRUTH, never the lists:
# predeclared names come from lib/system/core.z, keywords and reserved words
# from zlexer.z's kwlookup and islookupReserved.
#
# Three renames in a row missed these files -- `.release` -> `.drop`, the
# camelCase view rename, and the arc that added `drop` -- because nothing
# checked. The visible result was `view` listed as BOTH reserved and
# predeclared, reserved winning, and every `.view` in the documentation
# rendering black on red.
define HIGHLIGHT_GUARD_SH
set -e
LC_ALL=C; export LC_ALL
D=$$(mktemp -d); trap 'rm -rf "$$D"' EXIT

# what a highlighter may carry that core.z does not define: the ownership and
# access markers, the context words, and `Any`, which is real and reachable
# without a core.z re-export.
CONTEXT="Any _ borrow copy drop generic hold iterator meta private public tag take this view yield"

sed -n 's|^syn match \([A-Za-z]*\) /\(.*\)/$$|\1 \2|p' editor/nvim/syntax/zerolang.vim > "$$D/vim.raw"
vimset() {
  awk -v g="$$1" '$$1 == g { $$1=""; print }' "$$D/vim.raw" \
    | sed 's/\\<//g; s/\\>//g; s/\\%(//g; s/)$$//' | tr '|' '\n' | sed 's/\\//g' \
    | grep -v '^ *$$' | tr -d ' ' | sort -u
}
jsset() {
  python3 -c "
import re,sys
s=open('docs/style/prism-zerolang.js').read()
m=re.search(r'var '+sys.argv[1]+r' = \[(.*?)\];',s,re.S)
print('\n'.join(sorted(set(re.findall(r\"'([^']*)'\",m.group(1))))))" "$$1"
}
rbset() {
  python3 -c "
import re,sys
s=open('editor/rouge/zerolang.rb').read()
m=re.search(r'def self\.'+sys.argv[1]+r'\b.*?%w\((.*?)\)',s,re.S)
print('\n'.join(sorted(set(m.group(1).split()))))" "$$1"
}

grep -oE '^[A-Za-z_][A-Za-z0-9_]*:' lib/system/core.z | sed 's/:$$//' | sort -u > "$$D/core"
sed -n '/^kwlookup: function/,/^}/p' lib/system/zlexer.z \
  | grep -oE 'sv == "[^"]+"' | sed 's/sv == //; s/"//g' | sort -u > "$$D/lexkw.all"
cp "$$D/lexkw.all" "$$D/lexkw"
sed -n '/^islookupReserved: function/,/^}/p' lib/system/zlexer.z \
  | grep -oE 'sv == "[^"]+"' | sed 's/sv == //; s/"//g' | sort -u > "$$D/lexres"

# the SPEC's two tables. Nothing gated them against the lexer, which is how
# `yield` sat in kwlookup while the spec called it a builtin function and both
# highlighters listed it as a predeclared identifier -- three sources agreeing
# and the implementation the odd one out, for as long as nobody looked.
specset() {
  awk -v h="$$1" '
    $$0 == "#---: " h { inh=1; next }
    inh && /^#---: / { exit }
    inh && /^#code/ { incode=1; next }
    inh && incode && /^$$/ { incode=0 }
    inh && incode { print }
  ' docs/spec.pdoc | tr -s ' ' '\n' | grep -v '^$$' | sort -u
}
specset Keywords > "$$D/speckw"; specset "Reserved Words" > "$$D/specres"

jsset keywords > "$$D/pkw"; jsset reserved > "$$D/pres"; jsset builtins > "$$D/pbi"
rbset keywords > "$$D/rkw"; rbset reserved > "$$D/rres"; rbset builtins > "$$D/rbi"
vimset zerolangKeyword > "$$D/vkw"; vimset zerolangReserved > "$$D/vres"
{ vimset zerolangBuiltinType; vimset zerolangBuiltinConst; vimset zerolangBuiltin; } | sort -u > "$$D/vbi"
echo "$$CONTEXT" | tr ' ' '\n' | grep -v '^$$' | sort -u > "$$D/ctx"

fail=0
cmp_set() {
  if ! cmp -s "$$2" "$$3"; then
    echo "highlight-guard FAIL: $$1"
    comm -23 "$$2" "$$3" | sed 's/^/    missing: /'
    comm -13 "$$2" "$$3" | sed 's/^/    stale:   /'
    fail=1
  fi
}
cmp_set "prism keywords vs zlexer kwlookup"         "$$D/lexkw"  "$$D/pkw"
cmp_set "nvim keywords vs zlexer kwlookup"          "$$D/lexkw"  "$$D/vkw"
cmp_set "prism reserved vs zlexer islookupReserved" "$$D/lexres" "$$D/pres"
cmp_set "nvim reserved vs zlexer islookupReserved"  "$$D/lexres" "$$D/vres"
cmp_set "spec Keywords vs zlexer kwlookup"          "$$D/lexkw"  "$$D/speckw"
cmp_set "spec Reserved Words vs zlexer islookupReserved" "$$D/lexres" "$$D/specres"
cmp_set "rouge keywords vs zlexer kwlookup"          "$$D/lexkw"  "$$D/rkw"
cmp_set "rouge reserved vs zlexer islookupReserved" "$$D/lexres" "$$D/rres"
cmp_set "prism builtins vs nvim builtins"           "$$D/pbi"    "$$D/vbi"
cmp_set "rouge builtins vs prism builtins"          "$$D/pbi"    "$$D/rbi"
sort -u "$$D/core" "$$D/ctx" > "$$D/want_bi"
cmp_set "builtins vs lib/system/core.z + context words" "$$D/want_bi" "$$D/pbi"

[ "$$fail" = 0 ] || { echo "  The language moved and a highlighter did not. Fix the word list."; exit 1; }
echo "highlight-guard OK: $$(wc -l < "$$D/core") core.z names, $$(wc -l < "$$D/lexkw") keywords, $$(wc -l < "$$D/lexres") reserved -- spec and all three highlighters agree"
endef
export HIGHLIGHT_GUARD_SH

highlight-guard:
	@sh -c "$$HIGHLIGHT_GUARD_SH"

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
#   ondemand    emitted only for an instance whose program CALLS it, so the
#               program this guard reads has no C function for it. Not the same
#               as unemitted: a program that calls it does get one, and if the
#               guard's own program ever starts calling it the entry becomes a
#               staleness error, which is the right way to be told
#   Type.method the receiver is projected and handed to another declared
#               method, which must itself be `.view` before this one may be
# A registered entry that turns out to HAVE a backing is an error too, so the
# register cannot go stale. String's comparisons are the reason the last kind
# exists: `s1 == s2` does not call z_String_eq (which nothing calls) -- it
# converts both sides to by-value views and calls z_StringView_eq.

VIEW_GUARD_PLACEHOLDER := z_List.c.tmpl=@@NAME@@:ListRef z_Map.c.tmpl=@@NAME@@:MapRR \
  z_MapIter.c.tmpl=@@NAME@@:MapRR,@@MAPKEYITER@@:MapKeyIter,@@MAPITEMITER@@:MapItemIter,@@MAPENTRY@@:MapEntry \
  z_Set.c.tmpl=@@NAME@@:SetRef,@@SETITER@@:SetIter \
  z_IdMap.c.tmpl=@@NAME@@:IdMapR \
  z_IdMapIter.c.tmpl=@@NAME@@:IdMapR,@@IDMAPITEMITER@@:IdMapItemIterR,@@IDMAPENTRY@@:IdMapEntryR \
  z_IdMapMut.c.tmpl=@@NAME@@:IdMapR \
  z_IdSet.c.tmpl=@@NAME@@:IdSet,@@IDSETITER@@:IdSetIter
VIEW_GUARD_EMITTED := get:ListRef.get,ListView.get getMut:ListRef.getMut,ListView.getMut \
  contains:ListRef.contains \
  listView:ListRef.listView sort:ListRef.sort iterate:ListRef.iterate \
  call:ListIter.call,ListIterVal.call \
  iterateMut:ListRef.iterateMut getv:MapRR.get eq:- extendView:- destroy:- \
  hasv:MapRR.has,SetRef.has deletev:SetRef.delete
VIEW_GUARD_BACKS := StringView.eq===,!= StringView.cmp=compare,<,<=,>,>=
VIEW_GUARD_INTERNAL := String.cat String.print String.free String.eq String.cmp \
  StringView.print StringView.indexOfRaw StringView.replaceImpl \
  ListRef.destroy ListRef.grow MapRR.destroy MapRR.grow MapRR.find \
  SetRef.destroy SetRef.grow SetRef.find \
  IdMapR.destroy IdMapR.grow IdMapR.find IdMapR.slot IdMapR.entries_cap \
  IdSet.destroy IdSet.grow IdSet.find IdSet.slot IdSet.items_cap
VIEW_GUARD_INLINE := Bytes.byteView:unemitted \
  ListRef.insert:ondemand ListRef.extend:ondemand \
  StringView.asString:inline \
  ListVal.append:ListRef.append ListVal.insert:ListRef.insert \
  ListVal.extend:ListRef.extend ListVal.get:ListRef.get ListVal.set:ListRef.set \
  ListVal.pop:ListRef.pop ListVal.contains:ListRef.contains \
  ListVal.getMut:ListRef.getMut \
  ListViewVal.get:ListView.get ListViewVal.getMut:ListView.getMut \
  ListViewVal.length:inline \
  ListVal.sort:ListRef.sort ListVal.listView:ListRef.listView \
  ListVal.iterate:ListRef.iterate ListVal.iterateMut:ListRef.iterateMut \
  ListVal.length:inline ListVal.capacity:inline \
  SetVal.add:SetRef.add SetVal.has:SetRef.has SetVal.delete:SetRef.delete \
  SetVal.iterate:SetRef.iterate SetIterVal.call:SetIter.call \
  SetVal.length:inline SetVal.capacity:inline \
  MapEntryRV.key:MapEntry.key MapEntryRV.value:MapEntry.value \
  MapEntryVR.key:MapEntry.key MapEntryVR.value:MapEntry.value \
  MapEntryVV.key:MapEntry.key MapEntryVV.value:MapEntry.value \
  IdMapEntryV.key:IdMapEntryR.key IdMapEntryV.value:IdMapEntryR.value \
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
  IdMapV.get:IdMapR.get IdMapV.set:IdMapR.set IdMapV.has:IdMapR.has \
  IdMapV.keyAt:IdMapR.keyAt IdMapV.valueAt:IdMapR.valueAt \
  IdMapR.length:inline IdMapR.capacity:inline \
  IdMapV.length:inline IdMapV.capacity:inline \
  IdSet.length:inline IdSet.capacity:inline \
  IdMapV.iterateItems:IdMapR.iterateItems IdMapV.getMut:IdMapR.getMut \
  IdMapItemIterV.call:IdMapItemIterR.call \
  String.length:inline String.capacity:inline String.stringView:inline \
  StringView.length:inline StringView.string:byvalue \
  String.contains:StringView.contains String.startsWith:StringView.startsWith \
  String.endsWith:StringView.endsWith String.count:StringView.count \
  String.hash:StringView.hash String.substring:StringView.substring \
  String.==:StringView.== String.!=:StringView.!= String.<:StringView.< \
  String.<=:StringView.<= String.>:StringView.> String.>=:StringView.>= \
  String.compare:StringView.compare \
  String.+:StringView.concat StringView.+:StringView.concat

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
    # the trailing boundary keeps a longer member spelt `this.take...` from
    # prefix-matching the receiver marker
    if ((dacc !~ /[{ ]:this[ }]/) && (dacc !~ /this\.(view|borrow|take)[^a-z]/)) return
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

    # A fragment spells its canonical types as `z_@Name@` holes, so the
    # receiver type derived from `z_@File@_t` still carries the hole markers:
    # the declaration it must match is the bare name. Only a single-@ hole is
    # unwrapped -- a `@@KEY@@` template placeholder keeps its spelling for the
    # ph lookup below.
    if (cty ~ /^@[A-Za-z_][A-Za-z0-9_]*@$$/) cty = substr(cty, 2, length(cty) - 2)

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
            print "view-guard FAIL: " dot " declares a receiver that reaches no C function the guard can read -- say why in VIEW_GUARD_INLINE (inline / byvalue / unemitted / ondemand / <Type.method> it delegates to)"
            bad = 1
            continue
        }
        nreg++
        why = rwhy[dot]
        if (why == "inline" || why == "byvalue" || why == "unemitted") continue
        if (why == "ondemand") continue
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
	  "$$VIEW_GUARD_AWK" lib/system/*.z lib/system/system/*.z src/zemitterc.z \
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
# emitFail line count in src/zemitterc.z.
#
# Two kinds of leg share that count, and they move in opposite directions. A
# FALLBACK leg is a construct the emitter cannot render: it may only DECREASE,
# and the baseline drops in the same commit that resolves one. A REFUSAL leg is
# the emitter declining to emit something that must never have reached it -- the
# `is native` declaration with no row, and the `fold` native nothing folded --
# which is the opposite of a silent degradation and is legitimately ADDED. A
# raise is therefore allowed only for a refusal, only with the leg named in the
# commit message, and the guard cannot tell the two apart: the prose is the
# check, so say which one it is.
FALLBACK_BASELINE :=
EMITFAIL_BASELINE := 30
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
# emitter references exists on disk, and no fragment on disk is unreferenced --
# a convention fragment is reached through the (unit, member) demand pair, so
# the member half alone counts as a reference, and a natives.tbl row naming the
# fragment in its `frag=` list counts as one too: that is where the names live
# now, so a guard reading only the emitter would call every renamed fragment an
# orphan. Leg 3: every `z_@Name@` hole a fragment
# spells names a canon the loader can actually fill -- a type declared in the
# convention units (core.z carries the io/system aliases: File, IoError,
# Reader, Writer, openmode, seekorigin, TextReader, Splitter, LinesIter,
# CpIter), an ioCanonTid arm (the generic instances, declared nowhere), or a
# name a loader binds explicitly (the mono/parse/codepoint stems). Derived
# from the declarations rather than a list, so a new fragment-backed type is
# legal the moment it is declared.
NATIVE_GUARD_EXCEPTIONS := io.print io.stdin io.stdout io.stderr os.env net.pollReadable
# reached by something other than a per-member demand: the statement-special and
# the three streams have no fragment of their own, and os.args is a bundle its
# emitter loads by hand (argv globals first).
CONVENTION_EXCEPTIONS := io.print io.stdin io.stdout io.stderr os.args

native-guard:
	@fail=0; conv=""; \
	for u in io os cli net tcc; do \
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
	  ref=0; \
	  grep -qF "\"$$stem\"" src/zemitterc.z && ref=1; \
	  grep -qF "\"$$need\"" src/zemitterc.z && ref=1; \
	  grep -qE "frag=([A-Z0-9_]+,)*$$stem(,|\]| )" src/runtime/natives.tbl && ref=1; \
	  for u in io os cli net tcc; do \
	    case "$$need" in "$$u"_*) \
	      grep -qF "memb: \"$${need#$$u\_}\"" src/zemitterc.z && ref=1;; \
	    esac; \
	  done; \
	  [ "$$ref" = 1 ] || { echo "native-guard: $$stem.inc on disk but nothing references it (orphan -- delete it or load it)"; fail=1; }; \
	done; \
	known=$$({ grep -oE 'if canon == "[A-Za-z_][A-Za-z0-9_]*"' src/zemitterc.z; \
	    grep -oE 'mono: "[A-Za-z_][A-Za-z0-9_]*"' src/zemitterc.z; \
	    grep -ohE 'bn\.append from: "[A-Za-z_][A-Za-z0-9_]*"' src/zemitterc.z; \
	  } | sed 's/.*"\(.*\)"/\1/'; \
	  sed -nE 's/^([A-Za-z_][A-Za-z0-9_]*):.*/\1/p' \
	    lib/system/core.z lib/system/io.z lib/system/os.z lib/system/net.z \
	    lib/system/cli.z lib/system/tcc.z lib/system/system.z; \
	  printf 'String\nStringView\n'); \
	known=" $$(echo "$$known" | sort -u | tr '\n' ' ') "; \
	nh=0; \
	for f in src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl src/runtime/*.tbl; do \
	  for h in $$(grep -ohE 'z_@[A-Za-z_][A-Za-z0-9_]*@' $$f | sed -e 's/^z_@//' -e 's/@$$//' | sort -u); do \
	    nh=$$((nh + 1)); \
	    case "$$known" in *" $$h "*) ;; \
	      *) echo "native-guard: $$f spells hole @$$h@, which names no known canon (declare the type, add its ioCanonTid arm, or bind it at the loader)"; fail=1;; \
	    esac; \
	  done; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "native-guard OK: native declarations and runtime fragments consistent (incl. no orphans, $$nh fragment holes known)"

# generic-param-guard -- L023 (a generic parameter's case follows its bound's)
# over the trees the linter is not pointed at. ZLSCOPE covers src/ and
# lib/system/, where L023 is a WARNING and `make check` already pins it at zero;
# examples/ and tests/fixtures/ are outside every lint gate, so a parameter
# renamed back there would regress in silence. This runs the SAME rule rather
# than restating it in grep -- one implementation, one place it can be wrong --
# and greps for the code because those trees carry other, pre-existing findings
# (32 L003s in examples/ alone) that would swamp an exit-status test.
# tests/fixtures/lsp_ws/lintgeneric is excluded BY DESIGN: it is L023's own
# fixture and has to contain violations for the rule to be pinned firing.
generic-param-guard: bin/zl
	@n=$$(bin/zl lint examples/*.z tests/fixtures/emitc_corpus/*.z \
	        tests/fixtures/errors/*.z 2>&1 | grep -c 'L023' || true); \
	if [ "$$n" -gt 0 ]; then \
	  echo "generic-param-guard FAIL: $$n generic parameter(s) whose case does not match their bound:"; \
	  bin/zl lint examples/*.z tests/fixtures/emitc_corpus/*.z \
	    tests/fixtures/errors/*.z 2>&1 | grep -A1 'L023' | sed 's/^/    /'; \
	  echo "  A parameter's case matches its bound's: see docs/styleguide.pdoc Generic Parameters."; \
	  exit 1; \
	fi; \
	echo "generic-param-guard OK: examples + corpus + error fixtures clean (baseline 0)"

# natives-tbl-guard -- src/runtime/natives.tbl answers "which implementation"
# for every operator the system units declare `is native`, keyed by qualified
# path. Both directions are checked: a declaration with no row would be found
# only by whichever program happens to use that operator, and a row naming no
# declaration is dead weight nothing can ever reach. Two-segment paths are the
# synthesised structural cases, which no unit declares, so they are exempt from
# the second leg by construction -- the check only looks at three-segment rows.
# LC_ALL=C throughout: the default collation ignores punctuation, so `sort -u`
# silently folds `.+`, `.-`, `.*` and `./` into one entry and the guard then
# compares 148 paths believing it compared 208.
#
# A fourth leg read every lib/system file and required a row for each `is
# native` it found. The compiler does that itself now, at the declaration and
# for every unit a program loads -- measured, not assumed: instrumented to
# report each path it checks, the corpus and the three drivers between them
# cover all 874, the same set this leg derived from the files. The one hole
# that measurement found was the check reading only a type's `as` block, so
# io.File's five natives -- declared in the class body -- were checked by this
# leg and by nothing else. It reads both blocks now.
natives-tbl-guard: bin/zc
	@fail=0; d=$$(mktemp -d); \
	for f in lib/system/system.z lib/system/system/*.z; do \
	  awk -v U=system '/^[A-Za-z_][A-Za-z0-9_]*: (record|variant|class)( |$$)/ {o=$$1; sub(/:$$/,"",o)} \
	    o != "" && /^    [-+*\/%&<>=!|^]+: function .*is native/ {op=$$1; sub(/:$$/,"",op); print U"."o"."op}' \
	    $$f; \
	done | LC_ALL=C sort -u > $$d/decl; \
	grep -oE '^\[[a-z]+\.[A-Za-z0-9_]+\.[-+*/%&<>=!|^]+' src/runtime/natives.tbl \
	  | sed 's/^\[//' | LC_ALL=C sort -u > $$d/rows; \
	miss=$$(LC_ALL=C comm -23 $$d/decl $$d/rows); \
	orph=$$(LC_ALL=C comm -13 $$d/decl $$d/rows); \
	if [ -n "$$miss" ]; then \
	  echo "natives-tbl-guard FAIL: declared native, no row in natives.tbl:"; \
	  echo "$$miss" | sed 's/^/    /'; fail=1; \
	fi; \
	if [ -n "$$orph" ]; then \
	  echo "natives-tbl-guard FAIL: row in natives.tbl names no native declaration:"; \
	  echo "$$orph" | sed 's/^/    /'; fail=1; \
	fi; \
	n=$$(wc -l < $$d/decl); rm -rf $$d; d2=$$(mktemp -d); \
	if [ $$fail -ne 0 ]; then \
	  echo "  Add the row, or drop it -- an operator resolves to its path and an"; \
	  echo "  absent path is 'no native implementation', not a fallthrough."; \
	  exit 1; \
	fi; \
	echo "natives-tbl-guard OK: $$n declared native operators, each with exactly one row"; \
	sed -n '/BEGIN GENERATED CONVERSIONS/,/END GENERATED CONVERSIONS/p' src/runtime/natives.tbl \
	  | sed '1,5d;$$d' > $$d2/section 2>/dev/null || true; \
	bin/zc natives > $$d2/gen; \
	if ! diff -q $$d2/section $$d2/gen >/dev/null; then \
	  echo "natives-tbl-guard FAIL: the conversions section is not the rule's output"; \
	  diff $$d2/section $$d2/gen | head -8; \
	  echo "  Run 'bin/zc natives' and replace the generated section -- or the rule moved"; \
	  echo "  and the table did not, which is the drift this check exists to catch."; \
	  exit 1; \
	fi; \
	cn=$$(grep -c "^\[" $$d2/gen); \
	NUM='^(i8|i16|i32|i64|i128|u8|u16|u32|u64|u128|c8|c32|f16|f32|f64|f128)$$'; \
	for f in lib/system/system.z lib/system/system/*.z; do \
	  awk -v U=system -v NUM="$$NUM" '/^[A-Za-z_][A-Za-z0-9_]*: (record|variant|class)( |$$)/ {o=$$1; sub(/:$$/,"",o)} \
	    o ~ NUM && /^    [a-z][a-z0-9]*: function \{:this\} out .* is native/ { \
	      n=$$1; sub(/:$$/,"",n); if (n ~ NUM) { k=($$0 ~ /resultval/) ? "lossy" : "safe"; print U"."o"."n" "k } }' \
	    $$f; \
	done | LC_ALL=C sort -u > $$d2/declkind; \
	grep '^\[' $$d2/gen \
	  | sed -E 's/^\[([^]]*)\] +\(\{.*/\1 lossy/; s/^\[([^]]*)\] +\(\(.*/\1 safe/' \
	  | LC_ALL=C sort -u > $$d2/rowkind; \
	nd=$$(wc -l < $$d2/declkind); nr=$$(wc -l < $$d2/rowkind); \
	if [ "$$nd" != "$$nr" ]; then \
	  echo "natives-tbl-guard FAIL: $$nd declared conversions but $$nr rows"; exit 1; \
	fi; \
	bad=$$(LC_ALL=C join -j1 -o 0,1.2,2.2 $$d2/declkind $$d2/rowkind 2>/dev/null | awk '$$2 != $$3'); \
	if [ -n "$$bad" ]; then \
	  echo "natives-tbl-guard FAIL: a conversion row disagrees with its declared return:"; \
	  echo "$$bad" | sed 's/^/    /' | head -8; \
	  echo "  A `resultval` declaration must build one; a direct one must be a plain cast."; \
	  exit 1; \
	fi; \
	rm -rf $$d2; \
	echo "natives-tbl-guard OK: $$cn generated conversion rows, each matching its declared return"; \
	d3=$$(mktemp -d); fail=0; \
	grep -oE '^\[[^]]*frag=[A-Z0-9_,]+' src/runtime/natives.tbl \
	  | sed -E 's/^\[([^ ]+).*frag=([A-Z0-9_,]+)/\1 \2/' > $$d3/fr; \
	while read path frags; do \
	  for f in $$(echo "$$frags" | tr ',' ' '); do \
	    test -f src/runtime/natives/$$f.inc || { \
	      echo "natives-tbl-guard FAIL: $$path names $$f, which is not on disk"; fail=1; }; \
	  done; \
	done < $$d3/fr; \
	nf=$$(wc -l < $$d3/fr); rm -rf $$d3; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "natives-tbl-guard OK: $$nf fragment-backed rows, every named fragment on disk"
