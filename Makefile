CC       := gcc
# the gcc spelling of the discarded-qualifiers error; clang names it differently.
QUALWERR := $(if $(findstring clang,$(shell $(CC) --version 2>/dev/null | head -1)),-Werror=incompatible-pointer-types-discards-qualifiers,-Werror=discarded-qualifiers)
CFLAGS_BASE := -std=c17 -Wall -Wextra -Wno-unused-function -Wno-unused-parameter \
            -Werror=implicit-function-declaration -Werror=implicit-int \
            -Werror=int-conversion -Werror=incompatible-pointer-types \
            -Werror=unused-but-set-variable
CFLAGS   := $(CFLAGS_BASE) $(QUALWERR)

# Parallel by default, for make and for the corpus runner's --jobs; NPROC=1 is serial.
NPROC    ?= $(shell nproc 2>/dev/null || echo 1)
MAKEFLAGS += -j$(NPROC)
# The three drivers (bin/zc, bin/zl, bin/zls) build at -O2; -fwrapv and
# -fno-strict-aliasing pin down the C the emitter relies on. Bootstrap
# intermediates and the test runner stay -O0: built once, run once.
OPTFLAGS := -O2 -fno-strict-aliasing -fwrapv
# The drivers emit their own Map/Set with the fast hash (their input is trusted
# source); everything else keeps the SipHash default. The C is identical either way.
ZCHASH   := --fast-hash
BUILDDIR := out
# The -l flags an emitted C file declares in its `zlink:` header (searched in the
# first lines, not pinned to one). Every rule linking emitted C uses ZLINKOF.
ZLINKSED = sed -n '1,8s|^/\* zlink: \(.*\) \*/$$|\1|p'
ZLINKOF = $$($(ZLINKSED) $(1) | tr ' ' '\n' | sed -e '/^$$/d' -e 's|^|-l|')
# The drivers link the vendored mimalloc ahead of libc; MIMALLOC=0 builds them on
# glibc. Nothing else uses it, and it never changes emitted C.
MIMALLOC ?= 1
ifeq ($(MIMALLOC),1)
MIMALLOC_OBJ := $(BUILDDIR)/mimalloc.o
else
MIMALLOC_OBJ :=
endif

# The allocation series is measured on a binary of its own, always gcc -O1, so a
# driver rebuilt at another level or by another compiler never reaches it.
PERFOPT  := -O1 -fno-strict-aliasing -fwrapv
PERFCC   ?= gcc
PERFBIN  := $(BUILDDIR)/zc-perf

# The commit `zc --version` names. It is linked in as a symbol (out/buildstamp.o,
# overriding a weak default) rather than passed as -D, which would recompile the
# drivers' single large translation unit on every commit.
BUILDID   := $(shell git rev-parse --short=8 HEAD 2>/dev/null)
BUILDID   := $(if $(BUILDID),$(BUILDID)$(shell git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || echo .dirty))
BUILDDATE := $(if $(BUILDID),$(shell git show -s --format=%cs HEAD 2>/dev/null))

# The bootstrap compiler, built from the committed seed (bootstrap/README.md).
ZC      := $(BUILDDIR)/zc-seed
# The runtime the emitter inlines: an edit here changes emitted C as a source edit does.
RT_DEP  := $(wildcard src/runtime/*.inc) $(wildcard src/runtime/*.c.tmpl) $(wildcard src/runtime/natives/*.inc) src/runtime/natives.tbl

# install tree. Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# examples/ minus the library-only units, which have no main.
SKIP     := mathutil genmath dissectlib
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: all check complexity-report style-lint-fast style-lint test ci ci-corpus \
	test-math-arm64 test-math-noasm test-portable-scalars docs-link-guard math-asm-guard \
	bench-math bench-math-kernels test-clang test-tcc test-tcc-heavy readable-check build \
	tcc zc zl zls regen-goldens regen-fmt-goldens regen-matrix matrix-guard regen-math-asm \
	fmt-raw-guard regen-lsp-goldens bump-seed test-bootstrap install docs warn-check perf \
	perf-strict pre-push perf-elision any-guard typename-guard emitter-guard frontend-guard \
	lifetime-guard user-native-guard case-guard require-guard mode-parity zlink-rules-guard \
	refusal-guard static-tcc-guard zlink-guard emit-set ident-set const-row-guard \
	deadcode-guard alias-label-guard fwd-shape-guard eager-guard eager-lib-guard \
	highlight-guard view-guard fallback-guard clean native-guard generic-param-guard \
	zl-full-guard natives-tbl-guard

# Keep the per-example .c intermediates.
.SECONDARY:

# The zerolang sources of the tools under tools/ (one program per directory).
TOOLSRC := $(wildcard tools/*/*.z)
TOOLDIRS := $(sort $(patsubst %/,%,$(dir $(TOOLSRC))))

# What the parse-tier linter reads.
ZLSCOPE := src/*.z lib/system/*.z lib/system/system/*.z lib/system/math/*.z tests/unit/*.z $(TOOLSRC)
# What the --full tier reads. It checks a file as the unit it is, so style-lint
# gives each tree its own roots.
ZLFULLSCOPE := src/*.z lib/system/*.z lib/system/system/*.z lib/system/math/*.z
# What the formatter checks; tests/fixtures/ is input and stays as written.
FMTSCOPE := src/*.z lib/system/*.z lib/system/system/*.z lib/system/math/*.z examples/*.z tests/unit/*.z $(TOOLSRC)

all: bin/zc bin/zl bin/zls

# check -- the fast pre-commit gate.
check: style-lint-fast complexity-report

# complexity-report -- every function over A008's threshold, highest first, into
# $(COMPLEXITY_TSV); the per-file ratchet is tests/fixtures/arch_baseline.txt.
COMPLEXITY_SCOPE := src/*.z lib/system/*.z lib/system/system/*.z lib/system/math/*.z tests/unit/*.z examples/*.z $(TOOLSRC)
COMPLEXITY_TSV := $(BUILDDIR)/cognitive-complexity.tsv
complexity-report: bin/zl
	@mkdir -p $(BUILDDIR)
	@{ printf 'score\tfile:line\tfunction\n'; bin/zl lint --complexity 15 $(COMPLEXITY_SCOPE) 2>&1 \
	  | grep -A1 'warning\[A008\]' | grep -v '^--$$' | paste - - \
	  | sed -E 's/.*function `([^`]+)` has cognitive complexity ([0-9]+).*--> ([^:]+:[0-9]+):.*/\2\t\3\t\1/' \
	  | sort -t "$$(printf '\t')" -k1,1nr; } > $(COMPLEXITY_TSV)
	@n=$$(($$(wc -l < $(COMPLEXITY_TSV)) - 1)); top=$$(sed -n '2p' $(COMPLEXITY_TSV) | cut -f1,3 | tr '\t' ' '); \
	  echo "complexity-report: $$n functions over 15, highest $$top -- $(COMPLEXITY_TSV)"

# style-lint-fast -- the parse-tier lint and the formatter check (docs/zl.pdoc).
# style-lint -- adds the typecheck tier, which needs each tree's roots.
style-lint-fast: bin/zl
	bin/zl lint $(ZLSCOPE)
	bin/zl format --check $(FMTSCOPE)

style-lint: bin/zl
	bin/zl lint --full --src src --system lib/system $(ZLFULLSCOPE)
	bin/zl lint --full --src tests/unit --src src --system lib/system tests/unit/*.z
	@for d in $(TOOLDIRS); do echo "bin/zl lint --full --src $$d --system lib/system $$d/*.z"; \
	  bin/zl lint --full --src $$d --system lib/system $$d/*.z || exit 1; done
	bin/zl format --check $(FMTSCOPE)

# out/ztestrunner -- the corpus runner (src/ztestrunner.z).
$(BUILDDIR)/ztestrunner: bin/zc src/ztestrunner.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c $(call ZLINKOF,$(BUILDDIR)/ztestrunner.c) -lm

# test -- the fast corpus gate. The arch and docs kinds run bin/zl, so it must be
# built: without it those kinds count nothing and every ratchet reads zero.
test: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC)

# ci -- every gate: the guards, the heavy corpus (self-host ASan and the
# fixpoint) and the seed bootstrap under gcc, clang and tcc, last and serial.
ci: style-lint zl-full-guard complexity-report warn-check emitter-guard typename-guard frontend-guard lifetime-guard native-guard alias-label-guard fwd-shape-guard generic-param-guard natives-tbl-guard const-row-guard view-guard fallback-guard highlight-guard any-guard deadcode-guard eager-guard eager-lib-guard case-guard user-native-guard zlink-guard zlink-rules-guard require-guard static-tcc-guard refusal-guard fmt-raw-guard readable-check perf-strict test-tcc-heavy test-clang test-math-arm64 test-math-noasm test-portable-scalars math-asm-guard matrix-guard docs-link-guard mode-parity ci-corpus
	$(MAKE) --no-print-directory test-bootstrap BOOTSTRAP_CCS="$(CI_BOOTSTRAP_CCS)"
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"

ci-corpus: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

# test-math-arm64 -- the math corpus cross-built for aarch64 and run under qemu
# against the same goldens, on the builtin word primitives and on the generated
# asm kernels. MATH_ARM64_REFUSED uses f128, which the backend refuses off
# x86-64: it must fail with that refusal. Skipped without the cross tools.
MATH_ARM64_DIR := $(BUILDDIR)/math-arm64
MATH_ARM64_REFUSED := math_constants_wide
test-math-arm64: bin/zc
	@if ! command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 || ! command -v qemu-aarch64 >/dev/null 2>&1; then \
	  echo "test-math-arm64: skipped (needs aarch64-linux-gnu-gcc and qemu-aarch64)"; exit 0; fi; \
	fail=0; n=0; \
	for v in builtins asm; do \
	  d=$(MATH_ARM64_DIR)/$$v; mkdir -p $$d; fl=""; \
	  if [ $$v = asm ]; then fl="--cflags -DZ_MATH_ASM_ARM64"; fi; \
	  for f in tests/fixtures/emitc_corpus/math/*.z; do \
	    b=$$(basename $$f .z); n=$$((n + 1)); \
	    bin/zc build $$f --target aarch64-linux-gnu $$fl -o $$d/$$b > $$d/$$b.build 2>&1; rc=$$?; \
	    case " $(MATH_ARM64_REFUSED) " in *" $$b "*) \
	      grep -q "which only x86_64 targets have" $$d/$$b.build && [ $$rc -eq 1 ] \
	        || { echo "test-math-arm64 FAIL ($$v): $$b must be refused for f128 (exit $$rc)"; fail=1; }; \
	      continue;; \
	    esac; \
	    if [ $$rc -ne 0 ]; then \
	      echo "test-math-arm64 FAIL ($$v): $$b does not build"; sed -n 1,3p $$d/$$b.build; fail=1; continue; fi; \
	    qemu-aarch64 -L /usr/aarch64-linux-gnu $$d/$$b > $$d/$$b.out 2>&1; \
	    cmp -s $$d/$$b.out tests/fixtures/run_golden/$$b.out \
	      || { echo "test-math-arm64 FAIL ($$v): $$b differs from its golden"; fail=1; }; \
	  done; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "test-math-arm64 OK: $$n math builds on aarch64 (builtins, asm), golden or refused as f128"

# test-math-noasm -- the math corpus on math's C kernels (-DZ_MATH_NOASM).
MATH_NOASM_DIR := $(BUILDDIR)/math-noasm
test-math-noasm: bin/zc
	@mkdir -p $(MATH_NOASM_DIR); fail=0; n=0; \
	for f in tests/fixtures/emitc_corpus/math/*.z; do \
	  b=$$(basename $$f .z); n=$$((n + 1)); \
	  if ! bin/zc build $$f --cflags -DZ_MATH_NOASM -o $(MATH_NOASM_DIR)/$$b > $(MATH_NOASM_DIR)/$$b.build 2>&1; then \
	    echo "test-math-noasm FAIL: $$b does not build"; sed -n 1,3p $(MATH_NOASM_DIR)/$$b.build; fail=1; continue; fi; \
	  $(MATH_NOASM_DIR)/$$b > $(MATH_NOASM_DIR)/$$b.out 2>&1; \
	  cmp -s $(MATH_NOASM_DIR)/$$b.out tests/fixtures/run_golden/$$b.out \
	    || { echo "test-math-noasm FAIL: $$b differs from its golden"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "test-math-noasm OK: $$n math programs on math's C kernels"

# test-portable-scalars -- every program naming i128, u128 or f16, built on
# their struct forms (what tcc always takes) and compared with the native goldens.
PORTABLE_SCALARS_DIR := $(BUILDDIR)/portable-scalars
test-portable-scalars: bin/zc
	@mkdir -p $(PORTABLE_SCALARS_DIR); fail=0; n=0; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z tests/fixtures/emitc_corpus/math/*.z; do \
	  b=$$(basename $$f .z); \
	  grep -qE '(^|[^A-Za-z0-9_])([iu]128|f16)([^A-Za-z0-9_]|$$)' $$f || continue; \
	  [ -f tests/fixtures/run_golden/$$b.out ] || continue; \
	  n=$$((n + 1)); \
	  if ! bin/zc build $$f --system lib/system --cflags "-DZ_INT128_PORTABLE -DZ_F16_PORTABLE" -o $(PORTABLE_SCALARS_DIR)/$$b > $(PORTABLE_SCALARS_DIR)/$$b.build 2>&1; then \
	    echo "test-portable-scalars FAIL: $$b does not build"; sed -n 1,3p $(PORTABLE_SCALARS_DIR)/$$b.build; fail=1; continue; fi; \
	  $(PORTABLE_SCALARS_DIR)/$$b > $(PORTABLE_SCALARS_DIR)/$$b.out 2>&1; \
	  cmp -s $(PORTABLE_SCALARS_DIR)/$$b.out tests/fixtures/run_golden/$$b.out \
	    || { echo "test-portable-scalars FAIL: $$b differs from its golden"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "test-portable-scalars OK: $$n programs on the struct forms of i128, u128 and f16"

# docs-link-guard -- every .pdoc link is bracketed (a bare one runs to the end of
# its line), and a target holding `#` is quoted.
docs-link-guard:
	@bad=$$(grep -nE '(^|[^[])#> to=' docs/*.pdoc); \
	if [ -n "$$bad" ]; then \
	  echo "docs-link-guard FAIL: a link written bare runs to the end of its line -- write [#> to=TARGET: text]:"; \
	  printf '%s\n' "$$bad" | sed 's/^/    /'; exit 1; fi; \
	bad=$$(grep -nE '\[#> to=[^" :]*#' docs/*.pdoc); \
	if [ -n "$$bad" ]; then \
	  echo "docs-link-guard FAIL: a target holding # must be quoted -- to=\"page.html#anchor\":"; \
	  printf '%s\n' "$$bad" | sed 's/^/    /'; exit 1; fi; \
	echo "docs-link-guard OK: every docs link is bracketed"

# math-asm-guard -- the committed asm fragments are what tools/asmgen writes.
MATH_ASM_CHECK_DIR := $(BUILDDIR)/asmgen-check
MATH_ASM_FRAGS := _Z_MATH_ARITH_AMD64.inc _Z_MATH_ARITH_ARM64.inc
math-asm-guard: out/asmgen
	@mkdir -p $(MATH_ASM_CHECK_DIR)
	@$(BUILDDIR)/asmgen $(MATH_ASM_CHECK_DIR)
	@for f in $(MATH_ASM_FRAGS); do \
	  if ! cmp -s $(MATH_ASM_CHECK_DIR)/$$f src/runtime/natives/$$f; then \
	    echo "math-asm-guard FAIL: src/runtime/natives/$$f is not what tools/asmgen writes -- make regen-math-asm"; \
	    diff $(MATH_ASM_CHECK_DIR)/$$f src/runtime/natives/$$f | head -20; exit 1; fi; \
	done
	@echo "math-asm-guard OK: $(MATH_ASM_FRAGS) are tools/asmgen's"

# bench-math -- math's operations timed per kernel tier (asm, builtins, portable,
# tcc). Not in ci: wall time is no ratchet.
BENCH_MATH_DIR := $(BUILDDIR)/bench-math
bench-math: bin/zc $(BUILDDIR)/tcc bench-math-kernels
	@mkdir -p $(BENCH_MATH_DIR)
	@bin/zc build tests/bench/math_bench.z --release -o $(BENCH_MATH_DIR)/asm
	@bin/zc build tests/bench/math_bench.z --release --cflags -DZ_MATH_NOASM -o $(BENCH_MATH_DIR)/builtins
	@bin/zc build tests/bench/math_bench.z --release --cflags -DZ_WORD_PORTABLE -o $(BENCH_MATH_DIR)/portable
	@bin/zc build tests/bench/math_bench.z --cc $(BUILDDIR)/tcc --tcc-lib $(TCCLIB) -o $(BENCH_MATH_DIR)/tcc
	@pin=""; if command -v taskset >/dev/null 2>&1; then pin="taskset -c $(BENCH_CPU)"; fi; \
	for v in asm builtins portable tcc; do echo "== $$v"; $$pin $(BENCH_MATH_DIR)/$$v; done

# bench-math-kernels -- the word-vector kernels timed alone, per tier; the median of
# REPS runs, pinned to BENCH_CPU (the host mixes two kinds of core).
REPS ?= 5
BENCH_CPU ?= 0
bench-math-kernels: $(BUILDDIR)/tcc
	@mkdir -p $(BENCH_MATH_DIR)
	@awk '/^\/\* 64x64 -> 128 multiply/,/^#endif/' src/runtime/z_hash.inc > $(BENCH_MATH_DIR)/mul128.h
	@grep -q 'z_fh_mul128' $(BENCH_MATH_DIR)/mul128.h \
	  || { echo "bench-math-kernels: z_hash.inc has no z_fh_mul128 block"; exit 1; }
	@inc="-I$(BENCH_MATH_DIR) -Isrc/runtime/natives"; \
	$(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function $$inc -o $(BENCH_MATH_DIR)/k-asm tests/bench/math_kernels.c \
	&& $(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function -DZ_MATH_NOASM $$inc -o $(BENCH_MATH_DIR)/k-builtins tests/bench/math_kernels.c \
	&& $(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function -DZ_WORD_PORTABLE $$inc -o $(BENCH_MATH_DIR)/k-portable tests/bench/math_kernels.c \
	&& $(BUILDDIR)/tcc -B $(TCCLIB) $$inc -o $(BENCH_MATH_DIR)/k-tcc tests/bench/math_kernels.c
	@pin=""; if command -v taskset >/dev/null 2>&1; then pin="taskset -c $(BENCH_CPU)"; fi; \
	for v in asm builtins portable tcc; do echo "== kernels $$v"; $$pin $(BENCH_MATH_DIR)/k-$$v -r $(REPS); done

# test-clang -- the corpus under clang. Only clang shows an f128 helper the
# prelude forgot to declare (gcc reads quadmath.h).
test-clang: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc clang --cc-forward --root . --jobs $(NPROC)

# what a corpus run under the vendored tcc needs built first.
TCC_RUN_DEPS := bin/zc bin/zl $(BUILDDIR)/tcc $(BUILDDIR)/ztestrunner

# test-tcc -- the corpus under the vendored tcc, as a backend (--cc-forward), so
# units tcc cannot build are refused by name. tests/tcc-known-failures.txt is
# the expected failure set; a move either way fails.
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

# test-tcc-heavy -- test-tcc with the self-host and fixpoint kinds (ci only).
# It names test-tcc's prerequisites itself so the parent make builds them before
# the sub-make starts; otherwise both link bin/zc at once.
test-tcc-heavy: $(TCC_RUN_DEPS)
	@$(MAKE) --no-print-directory test-tcc TCC_TIER=--heavy

# readable-check -- --readable-names programs build and behave as the default
# names do; the cases are the ones about naming (shadowing, case-only arm names,
# C-keyword members). Last, a readable-named compiler must emit identical C.
readable-check: bin/zc $(BUILDDIR)/buildstamp.o
	@mkdir -p $(BUILDDIR)/rn
	@for c in hello:examples vector:examples records:examples fibonacci:examples \
	          typedefs:examples shadow_unit_const:tests/fixtures/emitc_corpus \
	          rn_sibling_shadow:tests/fixtures/emitc_corpus \
          rn_two_companions:tests/fixtures/emitc_corpus \
	          arm_case_tags:tests/fixtures/emitc_corpus tag_stem_clash:tests/fixtures/emitc_corpus \
	          member_names_c_words:tests/fixtures/emitc_corpus \
	          generator_field_names:tests/fixtures/emitc_corpus; do \
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

# build -- compile every example, one pattern chain each.
EXDIR  := $(BUILDDIR)/ex
EXBINS := $(NAMES:%=$(EXDIR)/%.bin)

$(EXDIR)/%.c: examples/%.z bin/zc
	@mkdir -p $(EXDIR)
	bin/zc $* --src examples --system lib/system --emit-c $@

$(EXDIR)/%.bin: $(EXDIR)/%.c
	$(CC) $(CFLAGS) -o $@ $< \
	  $(call ZLINKOF,$<) -lm

build: $(EXBINS)
	@echo "$(words $(EXBINS)) examples built ($(EXDIR)/)"

# out/buildstamp.c is rewritten only when its text changes, so the drivers relink
# only when the commit or the tree's cleanliness moves.
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

# out/tcc + out/tcc-lib -- the vendored tinycc, built by upstream's own Makefile
# from a staged copy. GITHASH=no keeps our git state out of `tcc -v`;
# CPPFLAGS=-fPIC gives the driver and libtcc.so one set of objects; -j1 because
# libtcc1.a needs the built tcc. The payload dir holds everything zc needs.
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

$(TCCLIB)/libtcc.so: $(BUILDDIR)/tcc ;

tcc: $(BUILDDIR)/tcc $(TCCLIB)/libtcc.so
	@echo "vendored tcc: $(BUILDDIR)/tcc (payload $(TCCLIB))"

# out/zc-seed -- the bootstrap compiler, from the committed seed.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c $(call ZLINKOF,bootstrap/zc.c) -lm

# bin/zc -- the self-hosted compiler, built by the seed.
bin/zc.c: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) $(ZC) $(RT_DEP)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c

$(BUILDDIR)/zc.o: bin/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) $(OPTFLAGS) -c bin/zc.c -o $@

bin/zc: $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zc $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,bin/zc.c) -lpthread -lm

zc: bin/zc

# bin/zl -- the linter and formatter (src/zl.z); no emitter.
out/zl.c: $(BUILDDIR)/zc.o $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) $(RT_DEP) | bin/zc
	@mkdir -p out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c

$(BUILDDIR)/zl.o: out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zl.c -o $@

bin/zl: $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zl $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zl.c) -lpthread -lm

# bin/zls -- the language server (src/zls.z); no emitter.
out/zls.c: $(BUILDDIR)/zc.o $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) $(RT_DEP) | bin/zc
	@mkdir -p out
	bin/zc zls --src src --system lib/system $(ZCHASH) --emit-c out/zls.c

$(BUILDDIR)/zls.o: out/zls.c
	$(CC) $(CFLAGS) $(OPTFLAGS) -c out/zls.c -o $@

bin/zls: $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) -o bin/zls $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zls.c) -lpthread -lm

zl: bin/zl

zls: bin/zls

# The dump tools behind the lexer, parser and program goldens.
out/zlexer: bin/zc tests/unit/zlexer_dump.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zlexer_dump --src tests/unit --system lib/system --emit-c $(BUILDDIR)/zlexer.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zlexer $(BUILDDIR)/zlexer.c $(call ZLINKOF,$(BUILDDIR)/zlexer.c) -lm

out/zparser: bin/zc tests/unit/zparser_dump.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zparser_dump --src tests/unit --system lib/system --emit-c $(BUILDDIR)/zparser.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zparser $(BUILDDIR)/zparser.c $(call ZLINKOF,$(BUILDDIR)/zparser.c) -lm

# regen-goldens -- rewrite the lexer, parser and program goldens; review the diff.
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

# The formatter's dump tool, behind the fmt goldens and fmt-raw-guard.
out/zfmt: bin/zc tests/unit/zfmt_dump.z src/zfmt.z src/zfmtcursor.z src/zdoc.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc zfmt_dump --src tests/unit --src src --system lib/system --emit-c $(BUILDDIR)/zfmt.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/zfmt $(BUILDDIR)/zfmt.c $(call ZLINKOF,$(BUILDDIR)/zfmt.c) -lm

# regen-fmt-goldens -- rewrite the fmt goldens (a case's .args holds its flags).
regen-fmt-goldens: out/zfmt
	@for f in tests/fixtures/fmt_cases/*.z; do \
		name=$$(basename $$f .z); args=""; \
		[ -f tests/fixtures/fmt_cases/$$name.args ] && args=$$(cat tests/fixtures/fmt_cases/$$name.args); \
		$(BUILDDIR)/zfmt $$f $$args > tests/fixtures/fmt_golden/$$name.z; \
	done
	@echo "regenerated fmt goldens via $(BUILDDIR)/zfmt"

# The ownership matrix generator: every type family by every position.
out/matrixgen: bin/zc tests/unit/matrixgen.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc matrixgen --src tests/unit --system lib/system --emit-c $(BUILDDIR)/matrixgen.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/matrixgen $(BUILDDIR)/matrixgen.c $(call ZLINKOF,$(BUILDDIR)/matrixgen.c) -lm

# regen-matrix -- rewrite the matrix fixtures from tests/fixtures/matrix_expect.txt.
regen-matrix: out/matrixgen
	$(BUILDDIR)/matrixgen --root .

# matrix-guard -- the committed matrix fixtures are matrixgen's output, regenerated
# under a scratch root, with none left over that it no longer writes.
matrix-guard: out/matrixgen
	@d=$$(mktemp -d); mkdir -p $$d/tests/fixtures; \
	cp tests/fixtures/matrix_expect.txt tests/fixtures/run_cases.txt $$d/tests/fixtures/; \
	$(BUILDDIR)/matrixgen --root $$d > $$d/gen.log 2>&1 || { cat $$d/gen.log; rm -rf $$d; exit 1; }; \
	bad=0; n=0; \
	for f in $$(cd $$d && find tests/fixtures -type f ! -name matrix_expect.txt); do \
	  n=$$((n+1)); \
	  if ! cmp -s $$d/$$f $$f; then echo "matrix-guard FAIL: $$f differs from matrixgen's output"; bad=1; fi; \
	done; \
	for f in $$(find tests/fixtures/emitc_corpus tests/fixtures/run_golden tests/fixtures/errors -path '*matrix_*' -type f); do \
	  if [ ! -f $$d/$$f ]; then echo "matrix-guard FAIL: $$f is no longer generated"; bad=1; fi; \
	done; \
	rm -rf $$d; \
	if [ $$bad -ne 0 ]; then echo "  run 'make regen-matrix' and review the diff"; exit 1; fi; \
	echo "matrix-guard OK: $$n files equal matrixgen's output"

# tools/asmgen -- writes math's word-vector kernels as inline asm, per architecture.
out/asmgen: bin/zc $(wildcard tools/asmgen/*.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc asmgen --src tools/asmgen --system lib/system --emit-c $(BUILDDIR)/asmgen.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/asmgen $(BUILDDIR)/asmgen.c $(call ZLINKOF,$(BUILDDIR)/asmgen.c) -lm

regen-math-asm: out/asmgen
	$(BUILDDIR)/asmgen src/runtime/natives

# fmt-raw-guard -- RAW mode replays every token and its trivia, so the output must
# equal the input byte for byte; unlike the fmt goldens it survives layout changes.
fmt-raw-guard: out/zfmt
	@bad=0; n=0; \
	for f in $(FMTSCOPE); do \
	  n=$$(($$n + 1)); \
	  if ! $(BUILDDIR)/zfmt $$f --raw 2>/dev/null | cmp -s - $$f; then \
	    echo "  $$f"; bad=$$(($$bad + 1)); \
	  fi; \
	done; \
	if [ $$bad -gt 0 ]; then \
	  echo "fmt-raw-guard FAIL: $$bad of $$n file(s) do not survive a RAW round trip"; \
	  exit 1; \
	fi; \
	echo "fmt-raw-guard OK: $$n file(s) byte-identical through a RAW round trip"

# regen-lsp-goldens -- rewrite the lsp goldens (a full runner pass).
regen-lsp-goldens: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --regen-lsp

# bump-seed -- regenerate the committed seed from bin/zc; review the diff.
bump-seed: bin/zc
	bin/zc zc --src src --system lib/system --emit-c bootstrap/zc.c
	@echo "regenerated bootstrap/zc.c -- review the diff and commit"

# The compilers test-bootstrap proves the seed against: $(CC) locally, all three
# in ci. A named compiler that is missing fails rather than proving less.
BOOTSTRAP_CCS ?= $(CC)

CI_BOOTSTRAP_CCS ?= $(CC) clang $(BUILDDIR)/tcc

# test-bootstrap -- per compiler: cc the seed, bootstrap twice and require the
# fixpoint (b2 == b3), and check a seed-built zc compiles the ztypes smoke to its
# golden. Then across compilers: b1 must be byte-identical whichever compiler
# built the zc that emitted it. tcc gets -B and none of the -Werror pins.
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

# install -- a self-contained tree at $(ROOT) plus symlinks in $(BINDIR).
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

# docs -- render docs/*.pdoc to HTML (needs ../picodoc-c); commit the .html.
docs:
	$(MAKE) -C docs
	@echo "rendered docs/ -- commit the regenerated .html"

# warn-check -- the seed and the three drivers' C, with warnings as errors, under
# gcc, clang and tcc: each sees things the others do not. WARNSET_* is the set
# each compiler actually implements (tcc silently accepts any -W).
WARN_CCS ?= $(CC) clang $(BUILDDIR)/tcc

WARNSET_gcc   := $(CFLAGS_BASE) -Werror=discarded-qualifiers $(OPTFLAGS)
WARNSET_clang := $(CFLAGS_BASE) -Werror=incompatible-pointer-types-discards-qualifiers $(OPTFLAGS)
WARNSET_tcc   := -B $(TCCLIB) -Wall -Wunsupported \
                 -Werror=implicit-function-declaration -Werror=discarded-qualifiers

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

# perf -- the self-compile snapshot for docs/perf-baseline.md: line count, wall,
# peak RSS, phase split and the allocation total.
PERFARGS := zc --src src --system lib/system --emit-c /dev/null
PERFRUN  := $(PERFBIN) $(PERFARGS)

# The series binary: bin/zc.c compiled by PERFCC at PERFOPT.
$(PERFBIN): bin/zc.c $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o
	@$(PERFCC) -std=c17 -w $(PERFOPT) -o $@ $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o bin/zc.c $(call ZLINKOF,bin/zc.c) -lpthread -lm

perf: $(PERFBIN)
	@echo "== zerolang line count (.z) =="
	@lsrc=$$(cat src/*.z | wc -l); llib=$$(cat lib/system/*.z lib/system/system/*.z lib/system/math/*.z | wc -l); \
	  printf "  src/*.z: %s    lib/system/**/*.z: %s    total: %s\n" "$$lsrc" "$$llib" "$$((lsrc + llib))"
	@echo "== self-compile wall best-of-5 (mimalloc; drop run 1) + peak RSS =="
	@for i in 1 2 3 4 5; do /usr/bin/time -f "  %es  %MkB" $(PERFRUN) 2>&1 | tail -1; done
	@echo "== phase split (parse / typecheck / emit) =="
	@$(PERFBIN) zc --src src --system lib/system --time --emit-c /dev/null 2>&1 | tail -1 | sed 's/^/  /'
	@echo "== allocations (valgrind memcheck: total heap blocks for one self-compile) =="
	@if command -v valgrind >/dev/null 2>&1; then \
	  valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: /  /'; \
	else echo "  (valgrind not installed -- skipping alloc total)"; fi

# perf-strict -- the allocation ratchet: heap blocks for one self-compile of
# $(PERFBIN), bit-identical run to run. It refuses a clang-built binary (LLVM
# deletes write-only allocations; perf-elision measures that), a failed run and
# allocs != frees. Above ALLOC_BASELINE fails; a commit raising it says why in
# its message, and one lowering the count lowers it here.
ALLOC_BASELINE := 2143376
# ALLOC_LINE -- the one measurement every allocation number comes from.
ALLOC_LINE = valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: //'

perf-strict: $(PERFBIN)
	@command -v valgrind >/dev/null 2>&1 \
	  || { echo "perf-strict: valgrind is not installed -- the allocation ratchet cannot measure"; exit 1; }
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

# pre-push -- check, test and the allocation ratchet.
pre-push: check test perf-strict
	@echo "PRE-PUSH GREEN: check + test + perf-strict (allocations <= $(ALLOC_BASELINE))"

# perf-elision -- allocations LLVM deletes as write-only: bin/zc.c built twice by
# one clang, once with -fno-builtin-malloc. The difference belongs at zero.
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

# any-guard -- there is no `Any`: lib/system must not declare it or bound by it.
any-guard:
	@n=$$(grep -nE '^[[:space:]]*Any:|Any\.generic' lib/system/*.z lib/system/system/*.z lib/system/math/*.z | grep -vE ':[0-9]+: *#' | wc -l); \
	if [ "$$n" -gt 0 ]; then \
	  echo "any-guard FAIL: lib/system declares Any or bounds a parameter by it"; \
	  grep -nE '^[[:space:]]*Any:|Any\.generic' lib/system/*.z lib/system/system/*.z lib/system/math/*.z | grep -vE ':[0-9]+: *#'; \
	  echo "  A generic names the family it takes: anyval.generic or AnyRef.generic."; \
	  exit 1; \
	fi; \
	echo "any-guard OK: lib/system neither declares Any nor bounds by it"

# typename-guard -- a type is identified by its declaration, never by its spelling:
# a comparison against a zast.wellKnown ty* name is allowed only in TYPENAME_OK,
# each where the name is not a type identity (system-gated declarations, member and
# suffix vocabulary, bound families).
TYPENAME_OK := src/ztypecheck.z:resolveClass src/ztypecheck.z:resolveProtocol \
	src/ztypecheck.z:defTypetypeOf src/ztypecheck.z:resolveObjectDef \
	src/ztypecheck.z:paramIsBorrowReftype src/ztypecheck.z:isBoxTemplate \
	src/ztypecheck.z:checkDataBlockMember src/ztypecheck.z:checkMarkerMember \
	src/zemitterc.z:emitDataMember src/zemitterc.z:emitStrConvCall \
	src/zsource.z:bareZeroTypeName src/ztypecheck.z:constraintKindForId
typename-guard:
	@fail=0; n=0; \
	for e in $$(awk '/^[A-Za-z_][A-Za-z0-9_]*: function/ {fn=$$1; sub(":", "", fn)} \
	    /^[ \t]*#/ {next} \
	    /(==|!=)[ \t]*zast\.wellKnown\.slot\.ty[A-Za-z0-9]+|zast\.wellKnown\.slot\.ty[A-Za-z0-9]+[ \t]*(==|!=)/ {print FILENAME ":" fn}' \
	    src/*.z | sort -u); do \
	  n=$$((n + 1)); \
	  case " $(TYPENAME_OK) " in *" $$e "*) ;; \
	    *) echo "typename-guard FAIL: $$e compares a name id with a wellKnown type name -- decide by the declaration (stdTidRo, declaredBySystem, numKindOfTid), or add the function to TYPENAME_OK with why"; fail=1;; \
	  esac; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "typename-guard OK: $$n functions compare a wellKnown type name, each sanctioned"

# emitter-guard -- the emitter reads the checker's stamps and ids; each count is a
# remaining name-based site (lower the baseline as one goes). z_t{ literals are
# allowed only in the one C-name composer.
emitter-guard:
	@e2=$$(grep -c 'ztypecheck.walkLookupTyperef' src/zemitterc.z); \
	e6=$$(grep -c 'regNameOf' src/zemitterc.z); \
	e7=$$(grep -c 'mangleVarName :name' src/zemitterc.z); \
	e8=$$(grep -cF 'io.readText' src/zemitterc.z); \
	e10=$$(grep -vE '^[[:space:]]*#' src/zemitterc.z | grep -cE '\.data\.[a-z]'); \
	e11=$$(grep -c 'poolFind' src/zemitterc.z); \
	e12=$$(grep -cE 'aliasChildName :ast|aliasOrChild :ast' src/zemitterc.z); \
	g2=$$(grep -cF 'z_t\{' src/zemitterc.z); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "emitter-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "emitter-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "'z_t{' literals in src/zemitterc.z" "$$g2" 3; \
	chk "ztypecheck.walkLookupTyperef" "$$e2" 1; \
	chk "regNameOf" "$$e6" 5; \
	chk "mangleVarName (both inside varCName)" "$$e7" 2; \
	chk "io.readText" "$$e8" 3; \
	chk "literal arm members (.data.<arm>; memberC spells them)" "$$e10" 0; \
	chk "poolFind (a re-intern of a name the emitter held as an id, or table text)" "$$e11" 12; \
	chk "member-name TEXT (C spelling and natives.tbl keys only; look members up by aliasChildNameId)" "$$e12" 18; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A new name-resolution site was added to the emitter. Read the typechecker"; \
	  echo "  stamp (atomVariableId/atomUnitDefId/callKind), the canonical child id, or"; \
	  echo "  ctxCname instead of resolving by name."; \
	  exit 1; \
	fi; \
	echo "emitter-guard OK: zt=$$g2 walkLookup=$$e2 nameOf=$$e6 mangleVar=$$e7 readText=$$e8 armLiteral=$$e10 poolFind=$$e11 memberText=$$e12"

# frontend-guard -- the front end names no backend: no C spelling, no triple parse,
# no target (that is the generated `z` unit's). Comments are not counted.
FRONTEND_SRCS := src/ztypecheck.z src/ztyping.z src/ztypes.z src/zenv.z src/zgenerator.z \
	lib/system/zparser.z lib/system/zlexer.z lib/system/zast.z
frontend-guard:
	@c=$$(cat $(FRONTEND_SRCS) | grep -vE '^[[:space:]]*#' \
	  | grep -cE '__int128|_Float16|__float128|sizeof\(|_Alignof|void\*|u?int(8|16|32|64)_t|natives\.tbl|zlink|ccPath|ccMode|cckind|isCReserved|mangleVarName|ztarget\.'); \
	t=$$(cat $(FRONTEND_SRCS) | grep -vE '^[[:space:]]*#' | grep -cE 'target(Triple|Os|Arch)'); \
	fail=0; \
	if [ "$$c" -gt 0 ]; then \
	  echo "frontend-guard FAIL: $$c C spelling(s) in the front end's code:"; \
	  grep -nE '__int128|_Float16|__float128|sizeof\(|_Alignof|void\*|u?int(8|16|32|64)_t|natives\.tbl|zlink|ccPath|ccMode|cckind|isCReserved|mangleVarName|ztarget\.' $(FRONTEND_SRCS) \
	    | grep -vE ':[0-9]+:[[:space:]]*#' | sed 's/^/    /' | head -10; fail=1; fi; \
	if [ "$$t" -gt 0 ]; then \
	  echo "frontend-guard FAIL: the front end names the resolved target $$t times; it is the generated z unit's to say"; fail=1; fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  A backend fact belongs to the backend: the C emitter and its table (natives.tbl)."; \
	  exit 1; \
	fi; \
	echo "frontend-guard OK: no C spelling, no triple parse and no target in the front end's code"

# lifetime-guard -- places the emitter still decides a lifetime itself rather than
# reading the checker's destroy lists. isNonLvalueArg and bindingRhsIsBorrow are
# floors, not targets: both answer C-shape questions, not when anything dies.
lifetime-guard:
	@l1=$$(grep -c 'registerScopeDestroy' src/zemitterc.z); \
	l2=$$(grep -c 'refsLocal' src/zemitterc.z); \
	l3=$$(grep -c 'isNonLvalueArg' src/zemitterc.z); \
	l4=$$(grep -c 'bindingRhsIsBorrow' src/zemitterc.z); \
	l5=$$(grep -cF '_ah\{' src/zemitterc.z); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "lifetime-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "lifetime-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "registerScopeDestroy" "$$l1" 5; \
	chk "refsLocal" "$$l2" 4; \
	chk "isNonLvalueArg" "$$l3" 30; \
	chk "bindingRhsIsBorrow" "$$l4" 6; \
	chk "'_ah{' argument hoists" "$$l5" 0; \
	if [ "$$fail" = "1" ]; then \
	  echo "  The emitter decided a lifetime on its own again. Read the checker's destroy"; \
	  echo "  lists (scopeDestroy, exitDestroy) and the variable's recorded state instead."; \
	  exit 1; \
	fi; \
	echo "lifetime-guard OK: registerScopeDestroy=$$l1 refsLocal=$$l2 isNonLvalueArg=$$l3 bindingRhsIsBorrow=$$l4 argHoists=$$l5"

# user-native-guard -- a unit outside src/runtime with its own natives.tbl rows and
# fragments compiles, links and runs, one native in a hidden subunit. The runtime
# dir is built from src/runtime plus the fixture's row and fragment.
user-native-guard: bin/zc
	@d=$$(mktemp -d); fail=0; \
	mkdir -p $$d/rt; cp -r src/runtime/. $$d/rt/; \
	cat tests/fixtures/user_native/mystery.tbl >> $$d/rt/natives.tbl; \
	cp tests/fixtures/user_native/_Z_MYSTERY_*.inc $$d/rt/natives/; \
	bin/zc mystery --src tests/fixtures/user_native --system lib/system \
	  --runtime $$d/rt --emit-c $$d/mystery.c > $$d/emit.log 2>&1 \
	  || { echo "user-native-guard FAIL: emit"; sed -n 1,5p $$d/emit.log; fail=1; }; \
	if [ $$fail -eq 0 ]; then \
	  $(CC) $(CFLAGS) -o $$d/mystery $$d/mystery.c $(call ZLINKOF,$$d/mystery.c) -lm > $$d/cc.log 2>&1 \
	    || { echo "user-native-guard FAIL: the unit's fragment did not load (link)"; \
	         grep -m1 error $$d/cc.log; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  got=$$($$d/mystery | tr '\n' ' '); \
	  if [ "$$got" != "42 42 " ]; then \
	    echo "user-native-guard FAIL: ran but printed '$$got', want '42 42 '"; fail=1; fi; \
	fi; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "user-native-guard OK: a unit outside src/runtime links and runs its own natives, a hidden subunit's among them"

# case-guard -- every program declaring `main` is in a case list (run, smoke or
# dump), so something compiles it. A unit with no main is reached through others.
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

# require-guard -- how many programs are refused under --cc tcc: a unit's toolchain
# needs speak only for programs that reach it. zlink-guard is the other half.
REQUIRE_TCC_BASELINE := 8

require-guard: bin/zc
	@n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  if bin/zc emit $$f --system lib/system --cc tcc -o /dev/null 2>&1 | grep -qE 'error\[E0601\]|from the C compiler, and tcc has none'; then \
	    n=$$(($$n + 1)); rep="$$rep  $$b\n"; \
	  fi; \
	done; \
	if [ "$$n" -ne $(REQUIRE_TCC_BASELINE) ]; then \
	  echo "require-guard FAIL: $$n program(s) rejected under --cc tcc (baseline $(REQUIRE_TCC_BASELINE))"; \
	  printf "$$rep"; \
	  echo "  a unit's needs speak only for a program that REACHES it."; \
	  exit 1; \
	fi; \
	echo "require-guard OK: $$n programs rejected under --cc tcc (baseline $(REQUIRE_TCC_BASELINE))"

# mode-parity -- --cc-mode inproc builds the same PROGRAM as spawn: every run case
# is built both ways and its output and exit code compared (never the bytes).
# Refusals must match too. Only zc's temp-path pid is normalised.
mode-parity: bin/zc $(BUILDDIR)/tcc
	@mkdir -p $(BUILDDIR)/parity; n=0; bad=0; \
	while read -r name dir rest; do \
	  [ -n "$$name" ] || continue; \
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

# zlink-rules-guard -- every rule that links emitted C takes its -l set via ZLINKOF.
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

# refusal-guard -- zc refuses what it cannot honour, exit 2 and naming it, and the
# honoured counterpart still works: --ldflags under inproc, bad or ambiguous
# --target, tcc cross, f128 off x86-64, a trailing value flag, `zc explain` of a
# foreign code, a missing root, a missing unit. Not error fixtures: those only
# ever run --emit-c, which never reaches these paths.
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
	bin/zc emit tests/fixtures/emitc_corpus/math/math_constants_wide.z --target aarch64-linux-gnu \
	  --system lib/system -o $$d/q.c > $$d/q.log 2>&1; rc=$$?; \
	if [ $$rc -ne 1 ] || [ -e $$d/q.c ] || ! grep -q "which only x86_64 targets have" $$d/q.log; then \
	  echo "refusal-guard FAIL: f128 for a non-x86-64 target must be refused by name, exit 1 (got $$rc)"; \
	  cat $$d/q.log; bad=1; \
	fi; \
	if ! bin/zc env --target x86_64-w64-mingw32 | grep -q '^ZC_CC=x86_64-w64-mingw32-gcc$$'; then \
	  echo "refusal-guard FAIL: the documented <triple>-gcc cross path stopped resolving"; \
	  bin/zc env --target x86_64-w64-mingw32; bad=1; \
	fi; \
	bin/zc build os_platform --src examples --system lib/system --cc gcc \
	  --target x86_64-linux -o $$d/g > $$d/g.log 2>&1; rc=$$?; \
	if [ $$rc -ne 0 ] || [ "$$($$d/g | head -1)" != "os=linux" ]; then \
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
	for c in A001 L013; do \
	  bin/zc explain $$c > $$d/x.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 2 ] || ! grep -q "run \`zl explain $$c\`" $$d/x.log; then \
	    echo "refusal-guard FAIL: zc explain $$c must exit 2 pointing at zl explain, got $$rc"; \
	    cat $$d/x.log; bad=1; \
	  fi; \
	done; \
	for c in X0200 E02x0 E; do \
	  bin/zc explain $$c > $$d/x.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 2 ] || ! grep -q "not an error code: $$c" $$d/x.log; then \
	    echo "refusal-guard FAIL: zc explain $$c must be refused as no error code, got $$rc"; \
	    cat $$d/x.log; bad=1; \
	  fi; \
	done; \
	for c in E0200 e0200 0200 200; do \
	  if [ "$$(bin/zc explain $$c 2>&1 | head -1)" != "ownership error" ]; then \
	    echo "refusal-guard FAIL: zc explain $$c must explain E0200"; bad=1; \
	  fi; \
	done; \
	for root in "--system $$d/none" "--src $$d/none --system lib/system"; do \
	  bin/zc emit hello --src examples $$root > $$d/r.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 2 ] || grep -q zpanic $$d/r.log || ! grep -q "$$d/none' does not exist" $$d/r.log; then \
	    echo "refusal-guard FAIL: a missing root ($$root) must exit 2 naming it, got $$rc"; \
	    cat $$d/r.log; bad=1; \
	  fi; \
	done; \
	for cmd in emit build run dump; do \
	  bin/zc $$cmd nosuchunit --src examples --system lib/system > $$d/u.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 1 ] || grep -q zpanic $$d/u.log || ! grep -q "Unknown reference 'nosuchunit'" $$d/u.log; then \
	    echo "refusal-guard FAIL: zc $$cmd of a missing unit must report it and exit 1, got $$rc"; \
	    cat $$d/u.log; bad=1; \
	  fi; \
	done; \
	if [ $$bad -ne 0 ]; then \
	  echo "  zc never accepts a flag it will ignore: it names what it resolved and exits 2."; \
	  exit 1; \
	fi; \
	echo "refusal-guard OK: every unhonourable flag combination is refused, and its honoured counterpart still works"

# static-tcc-guard -- the vendored tinycc is LGPL-2.1, so the drivers may only
# dlopen it: no libtcc symbol may appear in them (see vendor/tinycc/VERSION.md).
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

# zlink-guard -- how many programs declare a link library: a unit's library goes to
# the programs that reach it and no others.
ZLINK_BASELINE := 8

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

# emit-set / ident-set -- the byte-identity oracle for refactors. `make emit-set
# OUT=dir [ZC=...]` emits every example, corpus program and driver; `make
# ident-set A=dir B=dir` diffs two sets with every minted id normalised away.
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

# const-row-guard -- a natives.tbl `const=` row is chosen exactly when its operand
# is a compile-time constant: const_shift_form has three non-constant shifts, each
# of which must bind an `_r` operand (a variant names its hole more than once).
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

# deadcode-guard -- no emitted statement is unreachable (clang's
# -Wunreachable-code; gcc never warns). Skipped without clang.
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

# alias-label-guard -- a mono is labelled by its template's declared name, never by
# the alias a use reached it through. A program declaring its own List/Map/Set
# keeps that head.
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

# fwd-shape-guard -- no struct is both emitted untagged and forward-declared (a
# forward of an anonymous struct names nothing). The scan closes each
# `typedef struct {` at the first following line starting with `}`.
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

# eager-guard -- every example, corpus program and user-units case emits C that
# compiles under --eager. EAGER_KNOWN is bad by design: each pins that an unused
# definition is never checked, which --eager then checks and refuses. A move
# either way fails.
EAGER_KNOWN := unused_definition_not_demanded unused_method_not_demanded generic_member_not_demanded generic_unit_member_not_demanded user_never_not_demanded user_unit_public_instance_lazy user_unit_public_reexport_lazy user_unit_namespace_lazy

eager-guard: bin/zc
	@d=$$(mktemp -d); bad=""; \
	uu=$$(awk '$$2 == "tests/fixtures/user_units" { print $$2 "/" $$1 ".z" }' tests/fixtures/run_cases.txt); \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z $$uu; do \
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
	echo "eager-guard OK: examples + corpus + user units emit AND compile under --eager ($(words $(EAGER_KNOWN)) known)"

# eager-lib-guard -- the same for lib/system and src, whose unused definitions
# nothing else checks. Subunits (lib/system/system/) are reached only through
# their parent, so they are no compilation root.
EAGER_LIB_KNOWN :=

eager-lib-guard: bin/zc
	@d=$$(mktemp -d); bad=""; \
	for f in lib/system/*.z src/*.z; do \
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
	  case " $(EAGER_LIB_KNOWN) " in *" $$b "*) ;; *) new="$$new $$b";; esac; \
	done; \
	gone=""; for k in $(EAGER_LIB_KNOWN); do \
	  case " $$bad " in *" $$k "*) ;; *) gone="$$gone $$k";; esac; \
	done; \
	if [ -n "$$new" ]; then \
	  echo "eager-lib-guard FAIL: bad under --eager and not known:$$new"; \
	  echo "  A definition nothing demands is checked HERE and nowhere else."; \
	  echo "  Fix it, do not add it to EAGER_LIB_KNOWN."; \
	  exit 1; \
	fi; \
	if [ -n "$$gone" ]; then \
	  echo "eager-lib-guard FAIL: known-bad now clean:$$gone"; \
	  echo "  Delete the row from EAGER_LIB_KNOWN in the same commit that fixed it."; \
	  exit 1; \
	fi; \
	echo "eager-lib-guard OK: lib/system + src emit AND compile under --eager ($(words $(EAGER_LIB_KNOWN)) known)"

# highlight-guard -- the spec and the three highlighters (prism, nvim, rouge) carry
# the language's vocabulary: keywords and reserved words from zlexer, predeclared
# names from core.z.
define HIGHLIGHT_GUARD_SH
set -e
LC_ALL=C; export LC_ALL
D=$$(mktemp -d); trap 'rm -rf "$$D"' EXIT

# what a highlighter may carry that core.z does not define: the context words.
CONTEXT="_ copy iterator meta public tag this yield"

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
# the keywords are what each kw*Id function returns, spelled beside its label in
# zast's wellKnown block (read from the construction, so a new family needs no edit).
wkspell() {
  sed -n '/^wellKnown: data {/,/^}/p' lib/system/zast.z | awk -v want="$$1" '
    BEGIN { n = split(want, a, " "); for (i = 1; i <= n; i++) w[a[i]] = 1 }
    match($$0, /^ +[A-Za-z0-9_]+: "/) {
      l = $$1; sub(/:$$/, "", l)
      if (l in w) { s = $$0; sub(/^[^"]*"/, "", s); sub(/"$$/, "", s); print s }
    }' | sort -u
}
kwlabels=$$(sed -n '/^kw[A-Za-z]*Id: function/,/^}/p' lib/system/zlexer.z \
  | grep -oE 'slot\.[A-Za-z0-9_]+' | sed 's/slot\.//' | tr '\n' ' ')
wkspell "$$kwlabels" > "$$D/lexkw.all"
cp "$$D/lexkw.all" "$$D/lexkw"
rslabels=$$(sed -n '/^isReservedId: function/,/^}/p' lib/system/zlexer.z \
  | grep -oE 'slot\.[A-Za-z0-9_]+' | sed 's/slot\.//' | tr '\n' ' ')
wkspell "$$rslabels" > "$$D/lexres"

# the spec's Keywords and Reserved Words tables.
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
cmp_set "prism keywords vs zlexer kwOfId"            "$$D/lexkw"  "$$D/pkw"
cmp_set "nvim keywords vs zlexer kwOfId"             "$$D/lexkw"  "$$D/vkw"
cmp_set "prism reserved vs zlexer isReservedId" "$$D/lexres" "$$D/pres"
cmp_set "nvim reserved vs zlexer isReservedId"  "$$D/lexres" "$$D/vres"
cmp_set "spec Keywords vs zlexer kwOfId"             "$$D/lexkw"  "$$D/speckw"
cmp_set "spec Reserved Words vs zlexer isReservedId" "$$D/lexres" "$$D/specres"
cmp_set "rouge keywords vs zlexer kwOfId"             "$$D/lexkw"  "$$D/rkw"
cmp_set "rouge reserved vs zlexer isReservedId" "$$D/lexres" "$$D/rres"
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

# view-guard -- a native receiver marked `.view` must be const in its C backing,
# and a const receiver must be marked `.view`. The C compiler then proves no
# write. Receivers are found by type (the first parameter whose struct is the
# function's prefix). Valtype natives are judged by their natives.tbl row.
#   PLACEHOLDER  the type each template placeholder stands for
#   EMITTED      the declarations each emitter-built function backs (- for none)
#   BACKS        one C function backing several declarations
#   INTERNAL     C helpers with no declaration
#   INLINE       receivers no readable C function receives, with why:
#                inline / byvalue / unemitted / ondemand / writes / Type.method
VIEW_GUARD_PLACEHOLDER := z_List.c.tmpl=@@NAME@@:ListRef z_Map.c.tmpl=@@NAME@@:MapRR \
  z_MapIter.c.tmpl=@@NAME@@:MapRR,@@MAPKEYITER@@:MapKeyIter,@@MAPITEMITER@@:MapItemIter,@@MAPENTRY@@:MapEntry \
  z_Set.c.tmpl=@@NAME@@:SetRef,@@SETITER@@:SetIter \
  z_IdMap.c.tmpl=@@NAME@@:IdMapR \
  z_IdMapIter.c.tmpl=@@NAME@@:IdMapR,@@IDMAPITEMITER@@:IdMapItemIterR,@@IDMAPENTRY@@:IdMapEntryR \
  z_IdMapMut.c.tmpl=@@NAME@@:IdMapR \
  z_IdSet.c.tmpl=@@NAME@@:IdSet,@@IDSETITER@@:IdSetIter
VIEW_GUARD_EMITTED := get:ListRef.get,ListView.get,SpanVal.get getMut:ListRef.getMut \
  slice:ListRef.slice,ListView.slice \
  contains:ListRef.contains \
  listView:ListRef.listView sort:ListRef.sort \
  iterate:ListRef.iterate,ListView.iterate,intdata.iterate,floatdata.iterate \
  index:intdata.index,floatdata.index \
  iterateReverse:ListRef.iterateReverse,ListView.iterateReverse \
  call:ListIter.call,ListIterVal.call \
  iterateMut:ListRef.iterateMut getv:MapRR.get eq:- \
  extendView:ListVal.extendView destroy:- \
  hasv:MapRR.has,SetRef.has deletev:SetRef.delete
VIEW_GUARD_BACKS := StringView.eq===,!= StringView.cmp=compare,<,<=,>,>=
VIEW_GUARD_INTERNAL := String.cat String.print String.free String.eq String.cmp \
  StringView.print StringView.indexOfRaw StringView.replaceImpl \
  ListRef.destroy ListRef.grow MapRR.destroy MapRR.grow MapRR.find \
  SetRef.destroy SetRef.grow SetRef.find \
  IdMapR.destroy IdMapR.grow IdMapR.find IdMapR.slot IdMapR.entries_cap \
  IdSet.destroy IdSet.grow IdSet.find IdSet.slot IdSet.items_cap
VIEW_GUARD_INLINE := Bytes.byteView:unemitted \
  array.listView:inline array.slice:inline array.mutView:writes array.mutSlice:writes \
  ListVal.mutView:inline ListVal.mutSlice:inline ListVal.resize:ondemand \
  SpanVal.length:inline SpanVal.split:inline SpanVal.listView:inline SpanVal.copyFrom:inline \
  SpanVal.set:ondemand SpanVal.slice:ondemand SpanVal.fill:ondemand SpanVal.clear:ondemand \
  ListRef.insert:ondemand ListRef.extend:ondemand \
  ListVal.copy:ondemand SetVal.copy:ondemand MapVV.copy:ondemand \
  ListVal.==:ondemand ListVal.!=:ondemand \
  IdMapV.copy:ondemand IdSet.copy:ondemand \
  StringView.asString:inline \
  ListVal.append:ListRef.append ListVal.insert:ListRef.insert \
  ListVal.extend:ListRef.extend ListVal.get:ListRef.get ListVal.set:ListRef.set \
  ListVal.pop:ListRef.pop ListVal.contains:ListRef.contains \
  ListVal.getMut:ListRef.getMut \
  ListViewVal.get:ListView.get ListViewVal.slice:ListView.slice \
  ListViewVal.iterate:ListView.iterate \
  ListViewVal.iterateReverse:ListView.iterateReverse \
  ListViewVal.length:inline \
  ListVal.sort:ListRef.sort ListVal.listView:ListRef.listView \
  ListVal.slice:ListRef.slice \
  ListVal.iterate:ListRef.iterate ListVal.iterateMut:ListRef.iterateMut \
  ListVal.iterateReverse:ListRef.iterateReverse \
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
  String.+:StringView.concat StringView.+:StringView.concat \
  array.get:inline array.length:inline array.set:writes \
  str.length:inline str.size:inline str.string:byvalue \
  str.stringView:inline str.substring:inline \
  optionval.or:byvalue resultval.orPanic:byvalue resultval.or:byvalue \
  intliteral.*:byvalue floatliteral.*:byvalue *.iterate:byvalue *.times:byvalue \
  intdata.length:inline intdata.array:inline intdata.tag:inline intdata.slot:inline \
  floatdata.length:inline floatdata.array:inline floatdata.slot:inline stringdata.*:inline

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

# A native method declaring a receiver: record whether the receiver carries
# the .view marker. dval says whether its type is a valtype, which decides
# which side proves the marker.
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
    istbl = (FILENAME ~ /natives\.tbl$$/)
    isdecl = (FILENAME ~ /\.z$$/) && !isemit
    if (isdecl) { dty = ""; grab = 0 }
    else if (!isemit && !istbl) {
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
        dty = $$1; sub(/:.*/, "", dty); dval[dty] = 0; grab = 0; next
    }
    if ($$0 ~ /^[A-Za-z][A-Za-z0-9]*: (record|variant|facet)/) {
        dty = $$1; sub(/:.*/, "", dty); dval[dty] = 1; grab = 0; next
    }
    # a typedef takes its base's family, which its name's casing spells
    if ($$0 ~ /^[A-Za-z][A-Za-z0-9]*: typedef /) {
        dty = $$1; sub(/:.*/, "", dty); dval[dty] = (dty ~ /^[a-z]/); grab = 0; next
    }
    if (dty == "") next
    if (grab) {
        dacc = dacc " " $$0
        dlines++
        if (dacc ~ /is native/) { grab = 0; declEmit() }
        else if (dacc ~ /\bis \{/ || dlines > 14) grab = 0
        next
    }
    if ($$0 ~ /^[ \t]+([A-Za-z_][A-Za-z0-9_]*|[=!<>+*\/%&|^-]+): function/) {
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

# ---------------- natives.tbl: the valtype side ----------------
# `[unit.Type.method attrs] body`. The receiver is the @V@ hole of a
# receiver-only row and the @L@ hole of a binop; a body that assigns,
# increments or takes the address of that hole writes through it. Only
# valtype rows are read here -- a class's proof is its C function above.

istbl {
    if ($$0 !~ /^\[[a-z]+\.[A-Za-z0-9]+\.[^] ]+/) next
    path = $$1; sub(/^\[/, "", path); sub(/\]$$/, "", path)
    if (split(path, seg, ".") != 3) next
    if (!(seg[2] in dval) || !dval[seg[2]]) next
    key = seg[2] " " seg[3]
    body = $$0; sub(/^\[[^]]*\]/, "", body)
    if (body ~ /[^ \t]/) nbody[key] = 1
    if (body ~ /@[VL]@[ \t]*(=[^=]|\+\+|--)|&@[VL]@|(\+\+|--)[ \t]*@[VL]@/) nwrites[key] = 1
    nrow[key] = "natives.tbl [" path "]"
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
        if ((t in dval) && dval[t]) {
            # a valtype: the natives.tbl body is the proof, else the register
            # says what the emitter-built C does
            isv = (dkind[key] == "view")
            if (key in nbody) {
                writes = (key in nwrites)
                src = nrow[key]
            } else {
                why = ""
                if (dot in rwhy) why = rwhy[dot]
                else if ((t ".*") in rwhy) why = rwhy[t ".*"]
                else if (("*." m) in rwhy) why = rwhy["*." m]
                if (why == "") {
                    print "view-guard FAIL: " dot " is a valtype native with no natives.tbl body and no VIEW_GUARD_INLINE entry -- say what the emitter-built C does with the receiver (writes / inline / byvalue)"
                    bad = 1
                    continue
                }
                if (why != "writes" && why != "inline" && why != "byvalue") {
                    print "view-guard FAIL: " dot " is registered as " why ", but a valtype receiver is written (writes) or read in place (inline / byvalue)"
                    bad = 1
                    continue
                }
                writes = (why == "writes")
                src = "the emitter-built C (" why ")"
            }
            nvalt++
            if (!writes && !isv) {
                print "view-guard FAIL: " src " never writes through the receiver but " dot " is not declared '.view' -- the marker is available and unused"
                bad = 1
            } else if (writes && isv) {
                print "view-guard FAIL: " dot " is declared '.view' but " src " writes through the receiver"
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
    printf "view-guard OK: %d native receivers const-checked in C (%d '.view'), %d by-value, %d registered, %d internal, %d valtype natives judged by their natives.tbl row or register entry\n", checked, nview, nval, nreg, nint, nvalt
}
endef
export VIEW_GUARD_AWK

view-guard:
	@awk -v PH='$(VIEW_GUARD_PLACEHOLDER)' -v ALIAS='$(VIEW_GUARD_BACKS)' \
	  -v INTERNAL='$(VIEW_GUARD_INTERNAL)' -v EMITTED='$(VIEW_GUARD_EMITTED)' \
	  -v INLINE='$(VIEW_GUARD_INLINE)' \
	  "$$VIEW_GUARD_AWK" lib/system/*.z lib/system/system/*.z lib/system/math/*.z src/zemitterc.z \
	  src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl \
	  src/runtime/natives.tbl

# fallback-guard -- the emitter never degrades silently: no example or driver C
# carries a live `/* zemitterc: */` marker (the emitter quoting itself in a string
# is not live), and the emitFail and marker-site counts in src/zemitterc.z are
# ratchets. A new refusal leg may raise EMITFAIL_BASELINE (say so in the commit);
# a new fallback leg may not.
EMITFAIL_BASELINE := 39
MARKER_BASELINE := 24
EXCS := $(NAMES:%=$(EXDIR)/%.c)
fallback-guard: $(EXCS) bin/zc bin/zl bin/zls
	@fail=0; \
	for f in $(EXCS); do \
	  if grep -q '/\* zemitterc: ' $$f; then \
	    echo "fallback-guard FAIL: $$(basename $$f) carries an emitter marker"; fail=1; \
	  fi; \
	done; \
	for d in bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c; do \
	  n=$$(grep '/\* zemitterc: ' $$d | grep -cv '"[[:space:]]*/\* zemitterc: '); \
	  if [ "$$n" -gt 0 ]; then \
	    echo "fallback-guard FAIL: $$d carries $$n live emitter marker(s)"; fail=1; \
	  fi; \
	done; \
	n=$$(grep -c 'emitFail' src/zemitterc.z); \
	if [ "$$n" -gt $(EMITFAIL_BASELINE) ]; then \
	  echo "fallback-guard FAIL: src/zemitterc.z emitFail lines = $$n (baseline $(EMITFAIL_BASELINE)) -- new fallback leg?"; fail=1; \
	elif [ "$$n" -lt $(EMITFAIL_BASELINE) ]; then \
	  echo "fallback-guard: emitFail lines = $$n < baseline $(EMITFAIL_BASELINE) -- lower EMITFAIL_BASELINE"; \
	fi; \
	m=$$(grep -c '/\* zemitterc: ' src/zemitterc.z); \
	if [ "$$m" -gt $(MARKER_BASELINE) ]; then \
	  echo "fallback-guard FAIL: src/zemitterc.z marker sites = $$m (baseline $(MARKER_BASELINE))"; \
	  echo "  A new marker site MUST record an emitFail beside it, or it degrades in"; \
	  echo "  silence where no file leg can see it. Bump MARKER_BASELINE and say so."; fail=1; \
	elif [ "$$m" -lt $(MARKER_BASELINE) ]; then \
	  echo "fallback-guard: marker sites = $$m < baseline $(MARKER_BASELINE) -- lower MARKER_BASELINE"; \
	fi; \
	if [ "$$fail" = "1" ]; then \
	  echo "  The emitter hit a construct it cannot emit. Fix the emission gap."; \
	  exit 1; \
	fi; \
	echo "fallback-guard OK: no emitter markers (emitFail legs $$n, marker sites $$m)"

clean:
	rm -rf $(BUILDDIR) bin

# native-guard -- natives are declaration-driven. Every `is native` free function
# in io/os/cli/net/tcc/math has its _Z_<UNIT>_<NAME>.inc fragment (bar the
# exceptions); every fragment is referenced (by the emitter or a natives.tbl
# frag= list); and every z_@Name@, @ARM_canon.arm@ and @MEMBER_canon.name@ hole a
# fragment spells names a declared type, arm or member. No fragment spells a tag
# constant or a generated struct's member literally.
NATIVE_GUARD_EXCEPTIONS := io.print io.stdin io.stdout io.stderr os.env net.pollReadable

native-guard:
	@fail=0; conv=""; \
	for u in io os cli net tcc math; do \
	  for n in $$(awk '/^[a-zA-Z][a-zA-Z0-9]*: function/ {name=$$1; sub(/:.*/,"",name); pending=1} pending && /is native/ {print name; pending=0} pending && /is \{/ {pending=0}' lib/system/$$u.z $$(ls lib/system/$$u/*.z 2>/dev/null)); do \
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
	  for u in io os cli net tcc math; do \
	    case "$$need" in "$$u"_*) \
	      grep -qF "memb: \"$${need#$$u\_}\"" src/zemitterc.z && ref=1;; \
	    esac; \
	  done; \
	  [ "$$ref" = 1 ] || { echo "native-guard: $$stem.inc on disk but nothing references it (orphan -- delete it or load it)"; fail=1; }; \
	done; \
	known=$$({ sed -nE 's/^\[@canon\.([A-Za-z_][A-Za-z0-9_]*) .*/"\1"/p' src/runtime/natives.tbl; \
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
	      *) echo "native-guard: $$f spells hole @$$h@, which names no known canon (declare the type, add its natives.tbl canon row, or bind it at the loader)"; fail=1;; \
	    esac; \
	  done; \
	done; \
	for f in src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl src/runtime/*.tbl; do \
	  if grep -qE 'Z_[A-Z0-9_]+_TAG_' $$f; then \
	    echo "native-guard: $$f spells a tag constant literally; name it @ARM_<canon>.<arm>@ (or @@<KEY>_SOME@@ in a template)"; fail=1; \
	  fi; \
	done; \
	na=0; \
	for f in src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl src/runtime/*.tbl; do \
	  for h in $$(grep -ohE '@ARM_[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*@' $$f | sed -e 's/^@ARM_//' -e 's/@$$//' | sort -u); do \
	    na=$$((na + 1)); canon=$${h%%.*}; arm=$${h#*.}; head=$${canon%%_*}; \
	    case "$$known" in *" $$canon "*) ;; \
	      *) echo "native-guard: $$f spells @ARM_$$h@, whose type $$canon names no known canon"; fail=1; continue;; \
	    esac; \
	    arms=" $$(awk -v h="$$head" '$$0 ~ ("^" h ": (variant|union) [{]") {on=1; next} on && /^[}]/ {on=0} on && /^    [A-Za-z][A-Za-z0-9]*: / {sub(/^    /, ""); sub(/:.*/, ""); printf "%s ", $$0}' lib/system/*.z lib/system/*/*.z) "; \
	    case "$$arms" in *" $$arm "*) ;; \
	      *) echo "native-guard: $$f spells @ARM_$$h@, but $$head declares no arm $$arm"; fail=1;; \
	    esac; \
	  done; \
	done; \
	nm=0; \
	for f in src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl src/runtime/*.tbl; do \
	  for h in $$(grep -ohE '@MEMBER_[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*@' $$f | sed -e 's/^@MEMBER_//' -e 's/@$$//' | sort -u); do \
	    nm=$$((nm + 1)); canon=$${h%%.*}; mem=$${h#*.}; head=$${canon%%_*}; \
	    case "$$known" in *" $$canon "*) ;; \
	      *) echo "native-guard: $$f spells @MEMBER_$$h@, whose type $$canon names no known canon"; fail=1; continue;; \
	    esac; \
	    mems=" $$(awk -v h="$$head" '$$0 ~ ("^" h ": (record|class|variant|union|protocol|facet) [{]") {on=1; next} on && /^[}]/ {on=0} on && /^    [A-Za-z_][A-Za-z0-9_]*: / {sub(/^    /, ""); sub(/:.*/, ""); printf "%s ", $$0}' lib/system/*.z lib/system/*/*.z) "; \
	    case "$$mems" in *" $$mem "*) ;; \
	      *) echo "native-guard: $$f spells @MEMBER_$$h@, but $$head declares no member $$mem"; fail=1;; \
	    esac; \
	  done; \
	done; \
	for f in src/runtime/natives/*.inc src/runtime/*.inc src/runtime/*.c.tmpl src/runtime/*.tbl; do \
	  if grep -qE '\.data\.[A-Za-z_]|(\.|->)in_[A-Za-z_]|vtable->[A-Za-z_]' $$f; then \
	    echo "native-guard: $$f spells a member of a generated struct literally (an arm in \`data\`, an inline arm, a vtable slot); name it @MEMBER_<canon>.<name>@ (or @OKMEMBER@/@ERRMEMBER@ in a conversion row)"; fail=1; \
	  fi; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "native-guard OK: native declarations and runtime fragments consistent (incl. no orphans, $$nh fragment holes known, $$na tag-constant holes name declared arms, $$nm member holes name declared members)"

# generic-param-guard -- L023 (a parameter's case follows its bound's) over the
# examples and fixtures, which no lint gate reads.
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

# zl-full-guard -- `zl lint --full` never passes a file it did not check: it runs
# with no flags from a foreign cwd and with no runtime dir, says why when it
# cannot, reports a dependency's error at that file, reports a generic body once,
# lints a subunit as itself, and places L003/L011 only in the --full tier.
ZLFULL_FIX := tests/fixtures/zl_full/tiered.z
ZLFULL_DEP := tests/fixtures/zl_full/depunit/depmain.z
ZLFULL_ONCE := tests/fixtures/zl_full/generic_once.z
ZLFULL_SUB := tests/fixtures/zl_full/subunit/host
ZLFULL_PUB := tests/fixtures/zl_full/pubhost.z
ZLFULL_ELIDE := tests/fixtures/zl_full/elide.z
ZLFULL_BZ := tests/fixtures/zl_full/bare_zero.z
ZLFULL_BZS := tests/fixtures/zl_full/bare_zero_shadow.z
zl-full-guard: bin/zl
	@d=$$(mktemp -d); fail=0; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_FIX) 2>&1); \
	if ! printf '%s\n' "$$out" | grep -q 'L030'; then \
	  echo "zl-full-guard FAIL: no flags, foreign cwd: the typecheck tier did not report L030:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full --system $$d/none $(CURDIR)/$(ZLFULL_FIX) 2>&1); rc=$$?; \
	if [ $$rc -eq 0 ] || ! printf '%s\n' "$$out" | grep -q 'did not run: the system directory'; then \
	  echo "zl-full-guard FAIL: a missing --system was not reported (rc=$$rc):"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && ZEROLANG_RUNTIME=$$d/none $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_FIX) 2>&1); \
	if ! printf '%s\n' "$$out" | grep -q 'L030'; then \
	  echo "zl-full-guard FAIL: with no runtime directory the typecheck tier did not run:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full --src $$d $(CURDIR)/$(ZLFULL_FIX) 2>&1); rc=$$?; \
	if [ $$rc -eq 0 ] || ! printf '%s\n' "$$out" | grep -q "did not run: unit 'tiered' is under none"; then \
	  echo "zl-full-guard FAIL: a unit under no --src root was not reported (rc=$$rc):"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_DEP) 2>&1); \
	if ! printf '%s\n' "$$out" | grep -q 'depunit/dep.z:3:10' || ! printf '%s\n' "$$out" | grep -q 'bad: "x" + n'; then \
	  echo "zl-full-guard FAIL: an error in a dependency unit was not shown at its own path and line:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_ONCE) 2>&1); \
	if [ "$$(printf '%s\n' "$$out" | grep -c 'L013')" != 1 ] || [ "$$(printf '%s\n' "$$out" | grep -c 'L022')" != 1 ]; then \
	  echo "zl-full-guard FAIL: a generic body's L013/L022 was not reported exactly once:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_SUB)/inner.z 2>&1); \
	if ! printf '%s\n' "$$out" | grep -q 'L013' || ! printf '%s\n' "$$out" | grep -q 'subunit/host/inner.z:4:5' || ! printf '%s\n' "$$out" | grep -q 'subunit/host/inner.z:11:20' || printf '%s\n' "$$out" | grep -q 'error\['; then \
	  echo "zl-full-guard FAIL: a subunit file was not linted as its parent's subunit, at its own path:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_SUB).z 2>&1); \
	if printf '%s\n' "$$out" | grep -q 'L015\|L013'; then \
	  echo "zl-full-guard FAIL: linting a unit reported its subunit's finding at the unit's own path:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_SUB)/orphan.z 2>&1); rc=$$?; \
	if [ $$rc -eq 0 ] || ! printf '%s\n' "$$out" | grep -q "did not run: no unit the roots load for 'host' comes from this file"; then \
	  echo "zl-full-guard FAIL: a subunit file its parent never names was not reported (rc=$$rc):"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_PUB) 2>&1); \
	if printf '%s\n' "$$out" | grep -q 'L012'; then \
	  echo "zl-full-guard FAIL: a hidden subunit named only from the public block was reported unused:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_ELIDE) 2>&1); \
	if [ "$$(printf '%s\n' "$$out" | grep -c 'L003')" != 6 ] || ! printf '%s\n' "$$out" | grep -q 'elide.z:19:23'; then \
	  echo "zl-full-guard FAIL: L003 did not report the fixture's six labelled first arguments:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	if $(CURDIR)/bin/zl lint $(CURDIR)/$(ZLFULL_ELIDE) 2>&1 | grep -q 'L003'; then \
	  echo "zl-full-guard FAIL: the parse tier reported L003, which needs the checker"; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_BZ) 2>&1); \
	if [ "$$(printf '%s\n' "$$out" | grep -c 'L011')" != 2 ]; then \
	  echo "zl-full-guard FAIL: L011 did not report the fixture's two bare-zero initializers:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_BZS) 2>&1); \
	if printf '%s\n' "$$out" | grep -q 'L011'; then \
	  echo "zl-full-guard FAIL: L011 offered a bare name the unit's own type takes:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	if $(CURDIR)/bin/zl lint $(CURDIR)/$(ZLFULL_BZ) 2>&1 | grep -q 'L011'; then \
	  echo "zl-full-guard FAIL: the parse tier reported L011, which needs the checker"; fail=1; fi; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "zl-full-guard OK: --full runs without flags from a foreign cwd and without a runtime, says why when it cannot, places a dependency's error in its own file, and lints a subunit as itself"

# natives-tbl-guard -- every native operator the system units declare has exactly
# one natives.tbl row and every row a declaration; the generated conversions
# section is `zc natives` output and matches each declared return; every frag=
# names a fragment on disk. LC_ALL=C: other collations fold punctuation in sort.
natives-tbl-guard: bin/zc
	@fail=0; d=$$(mktemp -d); \
	for f in lib/system/system.z lib/system/system/*.z lib/system/collections.z; do \
	  u=system; case "$$f" in *collections.z) u=collections;; esac; \
	  awk -v U=$$u '/^[A-Za-z_][A-Za-z0-9_]*: (record|variant|class)( |$$)/ {o=$$1; sub(/:$$/,"",o)} \
	    o != "" && /^    ([-+*\/%&<>=!|^]+|and|or|not): function .*is native/ {op=$$1; sub(/:$$/,"",op); print U"."o"."op}' \
	    $$f; \
	done | LC_ALL=C sort -u > $$d/decl; \
	grep -oE '^\[[a-z]+\.[A-Za-z0-9_]+\.([-+*/%&<>=!|^]+|and|or|not)[] ]' src/runtime/natives.tbl \
	  | sed -e 's/^\[//' -e 's/[] ]$$//' | LC_ALL=C sort -u > $$d/rows; \
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
	    o ~ NUM && /^    [a-z][a-z0-9]*: function \{(:this|t: this\.view)\} out .* is native/ { \
	      n=$$1; sub(/:$$/,"",n); if (n ~ NUM) { k=($$0 ~ /resultval/) ? "lossy" : "safe"; print U"."o"."n" "k } }' \
	    $$f; \
	done | LC_ALL=C sort -u > $$d2/declkind; \
	grep '^\[' $$d2/gen \
	  | sed -E 's/^\[([^] ]*)[^]]*\] +\(\{.*/\1 lossy/; t; s/^\[([^] ]*)[^]]*\] +.*/\1 safe/' \
	  | LC_ALL=C sort -u > $$d2/rowkind; \
	nd=$$(wc -l < $$d2/declkind); nr=$$(wc -l < $$d2/rowkind); \
	if [ "$$nd" != "$$nr" ]; then \
	  echo "natives-tbl-guard FAIL: $$nd declared conversions but $$nr rows"; exit 1; \
	fi; \
	bad=$$(LC_ALL=C join -j1 -o 0,1.2,2.2 $$d2/declkind $$d2/rowkind 2>/dev/null | awk '$$2 != $$3'); \
	if [ -n "$$bad" ]; then \
	  echo "natives-tbl-guard FAIL: a conversion row disagrees with its declared return:"; \
	  echo "$$bad" | sed 's/^/    /' | head -8; \
	  echo "  A `resultval` declaration must build one; a direct one must be a plain conversion."; \
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
