CC       := gcc
# Windows (MSYS2 make, UCRT64 gcc) is told apart by the OS variable Windows sets
# for every process. There the drivers link everything statically, as zc links
# a program built for Windows, so bin/zc.exe runs with no DLL beside it; and
# they need no pthread (the thread shim is native Win32 there) and no libm (the
# C runtime holds the math functions; tcc has no libm to name).
ifeq ($(OS),Windows_NT)
WINDOWS_HOST := 1
DRIVERLINK   := -static
THREADLIB    :=
LIBM         :=
EXE          := .exe
HOST_TARGET  := x86_64-w64-mingw32
HOST_OSNAME  := windows
else
WINDOWS_HOST :=
DRIVERLINK   :=
THREADLIB    := -lpthread
LIBM         := -lm
EXE          :=
HOST_TARGET  := x86_64-linux
HOST_OSNAME  := linux
endif
# the gcc spelling of the discarded-qualifiers error; clang names it differently.
QUALWERR := $(if $(findstring clang,$(shell $(CC) --version 2>/dev/null | head -1)),-Werror=incompatible-pointer-types-discards-qualifiers,-Werror=discarded-qualifiers)
# The emitted C writes only what `main` reaches (make cdead-guard, cc-unused-guard):
# an unused static function is an error. An unused local stays a warning: a
# binding whose reads all fold is the source's matter (-Wall).
CFLAGS_BASE := -std=c17 -Wall -Wextra -Werror=unused-function -Wno-unused-parameter \
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
# NATIVEC_INC -- where the C of rows zc carries is written for a C test to
# include (`zc natives show WHAT --c`, holes as written): the thread shim
NATIVEC_INC := $(BUILDDIR)/nativec
# the machine make runs on, as `uname -m` spells it
HOST_ARCH := $(shell uname -m)
# What an emitted C file links, read off its header (searched in the first
# lines, not pinned to one): the objects of the C sources its `zcsrc:` lines name
# -- built by `bin/zc cobj` with the compiler that links, $(2) or $(CC), and the
# sanitizer flags $(3) (a sanitizer sees only what it instrumented), asked only
# when there are any, so the seed's own C never needs bin/zc -- then
# the -l flags its `zlink:` line declares. Every rule linking emitted C uses
# ZLINKOF.
ZLINKSED = sed -n '1,8s|^/\* zlink: \(.*\) \*/$$|\1|p'
ZCSRCSED = sed -n '1,8s|^/\* zcsrc: \(.*\) \*/$$|\1|p'
ZLINKOF = $$($(ZCSRCSED) $(1) | grep -q . && bin/zc cobj $(1) --cc $(or $(2),$(CC)) $(if $(3),--cflags "$(3)")) \
  $$($(ZLINKSED) $(1) | tr ' ' '\n' | sed -e '/^$$/d' -e 's|^|-l|')
# MSVCLINKOF -- ZLINKOF's libraries as MSVC's cl names them, `<name>.lib` (the
# C it is used on, zc's own, names no C source)
MSVCLINKOF = $$($(ZLINKSED) $(1) | tr ' ' '\n' | sed -e '/^$$/d' -e 's|$$|.lib|')
# what every cl command line here carries, as zcc.msvcFlags gives zc's
MSVC_CFLAGS := -nologo -std:c17 -experimental:c11atomics -utf-8
# The drivers link the vendored mimalloc ahead of libc; MIMALLOC=0 builds them on
# glibc. Nothing else uses it, and it never changes emitted C. Off on Windows:
# its static override of malloc is not safe with the UCRT.
MIMALLOC ?= $(if $(WINDOWS_HOST),0,1)
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

# The commit `zc --version` names. It is linked in as a symbol (out/buildstamp.o)
# rather than passed as -D, which would recompile the drivers' single large
# translation unit on every commit: STAMPDEF, the same on every build, has the
# runtime declare the stamp instead of defining it.
STAMPDEF  := -DZC_BUILD_STAMP_LINKED
BUILDID   := $(shell git rev-parse --short=8 HEAD 2>/dev/null)
BUILDID   := $(if $(BUILDID),$(BUILDID)$(shell git diff --quiet 2>/dev/null && git diff --cached --quiet 2>/dev/null || echo .dirty))
BUILDDATE := $(if $(BUILDID),$(shell git show -s --format=%cs HEAD 2>/dev/null))

# The bootstrap compiler, built from the committed seed (bootstrap/README.md).
ZC      := $(BUILDDIR)/zc-seed
# The runtime the emitter inlines: an edit here changes emitted C as a source edit does.

# install tree. Override e.g. ROOT=/opt/zerolang BINDIR=/usr/local/bin.
ROOT     ?= $(HOME)/.local/lib/zerolang
BINDIR   ?= $(HOME)/.local/bin

# examples/ minus the library-only units, which have no main.
SKIP     := mathutil genmath dissectlib
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(filter-out $(SKIP),$(basename $(notdir $(EXAMPLES))))

.PHONY: all check complexity-report style-lint-fast style-lint test doc-check ci ci-gates ci-corpus \
	test-math-arm64 test-sqlite-arm64 test-mingw test-tcc-mingw test-msvc-wine test-msvc-corpus ci-windows windows-guest windows-due test-math-noasm test-portable-scalars docs-link-guard math-asm-guard \
	bench-math bench-math-kernels test-clang test-tcc test-tcc-heavy readable-check build \
	tcc zc zl zls regen-goldens regen-fmt-goldens regen-matrix matrix-guard regen-constparity constparity-guard regen-math-asm \
	fmt-raw-guard regen-lsp-goldens bump-seed test-bootstrap install docs warn-check pedantic-check perf \
	perf-strict pre-push perf-elision any-guard typename-guard emitter-guard frontend-guard \
	lifetime-guard user-native-guard shim-guard thread-tsan-guard cobj-guard sqlite-decls-guard case-guard require-guard mode-parity zlink-rules-guard \
	refusal-guard static-tcc-guard zlink-guard emit-set ident-set const-row-guard \
	deadcode-guard cdead-guard cc-unused-guard piece-guard static-value-guard alias-label-guard fwd-shape-guard eager-guard eager-lib-guard \
	highlight-guard view-guard fallback-guard clean native-guard generic-param-guard \
	zl-full-guard

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
FMTSCOPE := src/*.z lib/system/*.z lib/system/system/*.z lib/system/math/*.z examples/*.z tests/unit/*.z $(TOOLSRC) lib/system/*.zd project.zd

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

# out/ztestrunner -- the corpus runner (src/ztestrunner.z, and src/zdocblocks.z
# for the doccode kind).
$(BUILDDIR)/ztestrunner: bin/zc src/ztestrunner.z src/zdocblocks.z src/zcc.z src/ztarget.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c $(call ZLINKOF,$(BUILDDIR)/ztestrunner.c) -lm

# test -- the fast corpus gate. The arch and docs kinds run bin/zl, so it must be
# built: without it those kinds count nothing and every ratchet reads zero.
test: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC)

# doc-check -- the doccode kind alone: every zerolang example in docs/*.pdoc and
# README.md emits with `zc emit --eager` (or fails as its expect= says) and is
# laid out as zl fmt lays it, with check=none and unformatted blocks ratcheted in
# tests/fixtures/doccode_baseline.txt. `make test` runs it too.
doc-check: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --doccode-only

# natives-show-guard -- `zc natives show` names where each thing it shows is
# written: each row `zc emit --mark-natives` marks in a program's C, shown, is
# placed at the file and line its mark names; a row's fragment's C is shown; a
# unit lists its rows; a name that is none of them is refused.
natives-show-guard: bin/zc
	@fail=0; d=$(BUILDDIR)/natives-show; mkdir -p $$d; \
	bin/zc emit examples/io_textreader.z --system lib/system --mark-natives -o $$d/marked.c || fail=1; \
	grep -E '^/\* nativec: (row|class) ' $$d/marked.c | sort -u | sed -E 's|^/\* nativec: (row\|class) (.*), (.*) \*/$$|\2  \3|' > $$d/rows.txt; \
	[ -s $$d/rows.txt ] || { echo "natives-show-guard FAIL: zc emit --mark-natives marked no row"; fail=1; }; \
	while IFS= read -r m; do \
	  bin/zc natives show "$${m%%  *}" --system lib/system | grep -qxF "$$m" \
	    || { echo "natives-show-guard FAIL: the mark says '$$m', zc natives show does not"; fail=1; }; \
	done < $$d/rows.txt; \
	bin/zc natives show io.eprintln --system lib/system | grep -q 'static void z_io_eprintln' \
	  || { echo "natives-show-guard FAIL: io.eprintln's fragment is not shown"; fail=1; }; \
	bin/zc natives show io --system lib/system | grep -q '^io.eprintln  ' \
	  || { echo "natives-show-guard FAIL: the unit io does not list io.eprintln"; fail=1; }; \
	bin/zc natives show io.nosuch --system lib/system > /dev/null 2>&1 \
	  && { echo "natives-show-guard FAIL: a row that does not exist was shown"; fail=1; }; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "natives-show-guard OK: zc natives show places each row where zc emit --mark-natives marks it, and shows its C"

# prelude-share-guard -- a parse that reads on from the prelude another parse
# kept (zparser.Parser.parseShared) builds the tree that parse would have built
# itself. `zc dump` of one program in two projects: in the first, the program's
# parse shares the project check's prelude; in the second, the project's
# systemDir names a copy of the stdlib, the lookups find other files, and the
# program reads the prelude again. The two models -- every node with the file
# it was read from -- are identical.
prelude-share-guard: bin/zc
	@d=$(BUILDDIR)/prelude-share; rm -rf $$d; mkdir -p $$d/shared $$d/own; fail=0; \
	cp -r lib/system $$d/sys; \
	printf 'project: (\n    toolchain.project\n    programs: list { "probe" }\n)\n' > $$d/shared/project.zd; \
	printf 'project: (\n    toolchain.project\n    systemDir: "../sys"\n    programs: list { "probe" }\n)\n' > $$d/own/project.zd; \
	for p in shared own; do \
	  cp examples/io_textreader.z $$d/$$p/probe.z; \
	  (cd $$d/$$p && env -u ZEROLANG_SYSTEM -u ZEROLANG_ROOT $(CURDIR)/bin/zc dump probe.z -o ../$$p.sql) \
	    || { echo "prelude-share-guard FAIL: zc dump in the $$p project"; fail=1; }; \
	done; \
	if [ $$fail -eq 0 ]; then \
	  cmp -s $$d/shared.sql $$d/own.sql \
	    || { echo "prelude-share-guard FAIL: the model read on from the shared prelude differs from the one read again"; \
	         diff $$d/shared.sql $$d/own.sql | head -4; fail=1; }; \
	fi; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "prelude-share-guard OK: a program reading on from the project check's prelude has the model it reads for itself"

# nativec-agree-guard -- the agree kind alone: every example and emitc_corpus
# program, for the host and for Windows, emits the same C from the rows zc
# carries as from the standard library's own nativec.zd files read as a user
# unit's are (`zc emit --nativec-files`): one walk registers both, so the two
# readers agree. A third emit, `--mark-natives`, must write marks and, without
# them, the same C. (bin/zc is rebuilt when a nativec.zd changes, so the rows
# it carries are the files' as they stand.)
nativec-agree-guard: bin/zc $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --agree-only

# ci -- every gate: the allocation ratchet first and alone, then the guards and
# the heavy corpus (self-host ASan and the fixpoint) in parallel, then the seed
# bootstrap under gcc, clang and tcc, last and serial. perf-strict runs alone
# because its self-compile reads and rewrites the project's generated copy of the
# `z` unit (.zerolang/), which every zc run in the repository root rewrites too:
# beside them its count moved by a few allocations, with the binary unchanged.
ci:
	$(MAKE) --no-print-directory perf-strict
	$(MAKE) --no-print-directory ci-gates
	$(MAKE) --no-print-directory test-bootstrap BOOTSTRAP_CCS="$(CI_BOOTSTRAP_CCS)"
	@echo "CI GATE GREEN: style-lint + corpus(--heavy: +selfhost-asan +fixpoint) + bootstrap"
	@$(MAKE) --no-print-directory windows-due

# ci-gates -- the gates ci runs in parallel, after perf-strict.
ci-gates: style-lint zl-full-guard complexity-report warn-check pedantic-check emitter-guard typename-guard frontend-guard lifetime-guard native-guard alias-label-guard fwd-shape-guard generic-param-guard const-row-guard view-guard fallback-guard highlight-guard any-guard deadcode-guard cdead-guard cc-unused-guard piece-guard static-value-guard eager-guard eager-lib-guard case-guard user-native-guard shim-guard thread-tsan-guard cobj-guard sqlite-decls-guard zlink-guard zlink-rules-guard require-guard static-tcc-guard refusal-guard fmt-raw-guard nativec-agree-guard natives-show-guard prelude-share-guard readable-check test-tcc-heavy test-clang test-signed-ub test-math-arm64 test-sqlite-arm64 test-math-noasm test-portable-scalars test-mingw test-tcc-mingw test-msvc-wine test-msvc-corpus math-asm-guard matrix-guard constparity-guard docs-link-guard mode-parity ci-corpus

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
	  if [ $$v = builtins ]; then fl="--cflags -DZ_MATH_NOASM"; fi; \
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

# test-sqlite-arm64 -- the sqlite corpus cross-built for aarch64 and run under
# qemu against the same goldens: the vendored library compiled by the cross
# compiler into the object cache and linked. Each program runs in the build
# directory, since one writes and removes a database file in its working
# directory. Skipped without the cross tools.
SQLITE_ARM64_DIR := $(BUILDDIR)/sqlite-arm64
test-sqlite-arm64: bin/zc
	@if ! command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 || ! command -v qemu-aarch64 >/dev/null 2>&1; then \
	  echo "test-sqlite-arm64: skipped (needs aarch64-linux-gnu-gcc and qemu-aarch64)"; exit 0; fi; \
	fail=0; n=0; d=$(SQLITE_ARM64_DIR); mkdir -p $$d; \
	for f in tests/fixtures/emitc_corpus/sqlite_*.z; do \
	  b=$$(basename $$f .z); n=$$((n + 1)); \
	  bin/zc build $$f --target aarch64-linux-gnu -o $$d/$$b > $$d/$$b.build 2>&1 \
	    || { echo "test-sqlite-arm64 FAIL: $$b does not build"; sed -n 1,3p $$d/$$b.build; fail=1; continue; }; \
	  (cd $$d && qemu-aarch64 -L /usr/aarch64-linux-gnu ./$$b) > $$d/$$b.out 2>&1; \
	  cmp -s $$d/$$b.out tests/fixtures/run_golden/$$b.out \
	    || { echo "test-sqlite-arm64 FAIL: $$b differs from its golden"; fail=1; }; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "test-sqlite-arm64 OK: $$n sqlite programs built for aarch64 match their goldens"

# the wine prefix the Windows legs share (shim-guard, test-mingw), created
# once and alone: wines that start in parallel on a missing prefix race to
# create it. Without wine there is nothing to create and no stamp is left.
WINE_PREFIX := $(abspath $(BUILDDIR))/wineprefix
WINE_ENV := DISPLAY= WINEDEBUG=-all WINEPREFIX=$(WINE_PREFIX)
$(BUILDDIR)/wineprefix/.ready:
	@if command -v wine >/dev/null 2>&1; then \
	  mkdir -p $(WINE_PREFIX) && $(WINE_ENV) timeout 300 wineboot -i > /dev/null 2>&1 && touch $@; \
	fi

# test-mingw -- the RUN corpus built for Windows by mingw-w64 against the UCRT
# (zc --target x86_64-w64-mingw32) and run under wine against the same goldens (Windows'
# own `<name>.windows.out` where a case has one). A case marked `posix` in
# run_cases.txt is listed as `(posix)` rather than run.
# tests/mingw-known-failures.txt is the expected failure set; a move either way
# fails. Skipped without the cross compiler and wine.
MINGW_CC := x86_64-w64-mingw32-gcc
# The packaged x86_64-w64-mingw32-gcc links msvcrt.dll by default, but every
# real Windows build -- UCRT64 gcc, MSVC, the vendored tcc -- links the
# Universal C Runtime. -mcrtdll=ucrt (GCC 14 and newer) makes the same gcc
# compile against mingw-w64's UCRT headers and link libucrt.a.
# MINGW_UCRT_CHECK is a shell test: does this gcc accept the flag?
MINGW_CFLAGS := -mcrtdll=ucrt
MINGW_UCRT_CHECK := $(MINGW_CC) $(MINGW_CFLAGS) -E -x c /dev/null > /dev/null 2>&1
MINGW_UCRT_FIX := needs mingw-w64 gcc 14 or newer for -mcrtdll=ucrt, as Ubuntu 26.10's gcc-mingw-w64-x86-64
MINGW_KNOWN := tests/mingw-known-failures.txt
test-mingw: bin/zc $(BUILDDIR)/ztestrunner $(BUILDDIR)/wineprefix/.ready
	@if [ -n "$(WINDOWS_HOST)" ]; then \
	  echo "test-mingw: skipped (Linux only: it runs Windows builds under wine; on Windows make test runs them natively)"; exit 0; fi; \
	if ! command -v $(MINGW_CC) >/dev/null 2>&1 || ! command -v wine >/dev/null 2>&1; then \
	  echo "test-mingw: skipped (needs $(MINGW_CC) and wine: sudo apt install gcc-mingw-w64-x86-64 wine; see tools/windows/README.md)"; exit 0; fi; \
	if ! $(MINGW_UCRT_CHECK); then \
	  echo "test-mingw: skipped ($(MINGW_CC) cannot target the UCRT: $(MINGW_UCRT_FIX); see tools/windows/README.md)"; exit 0; fi; \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS)" --target x86_64-w64-mingw32 \
	   --exec wine --run-only --root . --jobs $(NPROC) > $(BUILDDIR)/mingw-run.log 2>&1; \
	awk '/^FAIL/ || /^SKIP\(run\) .* \(posix\)$$/ { k = $$1; sub(/^FAIL\(/, "", k); sub(/\)$$/, "", k); \
	       print $$2, (NF >= 3 ? $$3 : "(" k ")") }' $(BUILDDIR)/mingw-run.log \
	  | sort > $(BUILDDIR)/mingw-fails.txt; \
	grep -v '^#' $(MINGW_KNOWN) | grep -v '^ *$$' | sort > $(BUILDDIR)/mingw-known.txt; \
	if ! diff -u $(BUILDDIR)/mingw-known.txt $(BUILDDIR)/mingw-fails.txt; then \
	  echo "test-mingw FAIL: the Windows failure set moved (-known +actual above)"; \
	  echo "  a gained line is a regression; a lost one passes now -- delete it from"; \
	  echo "  $(MINGW_KNOWN) in the same commit. Full log: $(BUILDDIR)/mingw-run.log"; \
	  exit 1; \
	fi; \
	echo "test-mingw OK: $$(sed -n 's/^pass=\([0-9]*\).*/\1/p' $(BUILDDIR)/mingw-run.log) RUN cases pass on Windows (mingw-w64, wine), $$(grep -c . $(BUILDDIR)/mingw-fails.txt) known failures, none new"

# test-tcc-mingw -- the RUN corpus built for Windows by the vendored tcc (the
# cross compiler above, which links the UCRT, as MSVC and MSYS2 UCRT64 gcc do) as a backend
# (--cc-forward, so units tcc cannot build are refused by name), run under wine
# against the same goldens. tests/tcc-mingw-known-failures.txt is the expected
# failure set: test-tcc's, plus what a Windows build reaches that a Linux one
# does not, each explained there. Skipped without wine.
TCC_MINGW_KNOWN := tests/tcc-mingw-known-failures.txt
test-tcc-mingw: bin/zc $(BUILDDIR)/ztestrunner $(BUILDDIR)/wineprefix/.ready
	@if [ -n "$(WINDOWS_HOST)" ]; then \
	  echo "test-tcc-mingw: skipped (Linux only: it runs Windows builds under wine; on Windows make test-tcc runs them natively)"; exit 0; fi; \
	if ! command -v wine >/dev/null 2>&1; then \
	  echo "test-tcc-mingw: skipped (needs wine: sudo apt install wine; see tools/windows/README.md)"; exit 0; fi; \
	$(MAKE) --no-print-directory $(BUILDDIR)/x86_64-win32-tcc > $(BUILDDIR)/tcc-mingw-build.log 2>&1 \
	  || { tail -20 $(BUILDDIR)/tcc-mingw-build.log; echo "test-tcc-mingw FAIL: the cross tcc did not build"; exit 1; }; \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(BUILDDIR)/x86_64-win32-tcc --cc-forward \
	   --ccflags "-B $(TCCW_LIB)" --target x86_64-w64-mingw32 \
	   --exec wine --run-only --root . --jobs $(NPROC) > $(BUILDDIR)/tcc-mingw-run.log 2>&1; \
	awk '/^FAIL/ || /^SKIP\(run\) .* \(posix\)$$/ { k = $$1; sub(/^FAIL\(/, "", k); sub(/\)$$/, "", k); \
	       print $$2, (NF >= 3 ? $$3 : "(" k ")") }' $(BUILDDIR)/tcc-mingw-run.log \
	  | sort > $(BUILDDIR)/tcc-mingw-fails.txt; \
	grep -v '^#' $(TCC_MINGW_KNOWN) | grep -v '^ *$$' | sort > $(BUILDDIR)/tcc-mingw-known.txt; \
	if ! diff -u $(BUILDDIR)/tcc-mingw-known.txt $(BUILDDIR)/tcc-mingw-fails.txt; then \
	  echo "test-tcc-mingw FAIL: the Windows tcc failure set moved (-known +actual above)"; \
	  echo "  a gained line is a regression; a lost one passes now -- delete it from"; \
	  echo "  $(TCC_MINGW_KNOWN) in the same commit. Full log: $(BUILDDIR)/tcc-mingw-run.log"; \
	  exit 1; \
	fi; \
	echo "test-tcc-mingw OK: $$(sed -n 's/^pass=\([0-9]*\).*/\1/p' $(BUILDDIR)/tcc-mingw-run.log) RUN cases pass on Windows (tcc, wine), $$(grep -c . $(BUILDDIR)/tcc-mingw-fails.txt) known failures, none new"

# test-msvc-wine -- MSVC itself, cl.exe run under wine (msvc-wine; MSVC_WINE
# names its bin/x64 directory), builds the thread shim's test with the flags
# windows-guest gives cl on Windows, and the result runs under wine. Paths stay
# relative: link.exe reads an argument starting with / as an option. Skipped
# without MSVC_WINE or wine.
MSVC_WINE ?=
test-msvc-wine: bin/zc $(BUILDDIR)/wineprefix/.ready $(NATIVEC_INC)/zthread_shim.inc
	@if [ -n "$(WINDOWS_HOST)" ]; then \
	  echo "test-msvc-wine: skipped (Linux only: it runs MSVC under wine; on Windows windows-guest builds with cl itself)"; exit 0; fi; \
	if [ -z "$(MSVC_WINE)" ] || [ ! -x "$(MSVC_WINE)/cl" ] || ! command -v wine > /dev/null 2>&1; then \
	  echo "test-msvc-wine: skipped (needs wine and MSVC_WINE=<dir>/msvc/bin/x64 from tools/windows/msvc-wine.sh; see tools/windows/README.md)"; exit 0; fi; \
	d=$(BUILDDIR)/msvc; rm -rf $$d; mkdir -p $$d; \
	$(WINE_ENV) $(MSVC_WINE)/cl -nologo -std:c17 -experimental:c11atomics -W3 -O2 -I$(NATIVEC_INC) \
	  tests/c/zthread_shim_test.c -Fe:$$d/shim.exe -Fo:$$d/ > $$d/cl.log 2>&1 \
	  || { echo "test-msvc-wine FAIL: cl did not build the shim test"; grep -m5 -E 'error|fatal' $$d/cl.log; exit 1; }; \
	$(WINE_ENV) timeout 300 wine $$d/shim.exe > $$d/run.log 2>&1 && grep -q "zthread shim OK" $$d/run.log \
	  || { echo "test-msvc-wine FAIL: the MSVC build of the shim test under wine"; tail -20 $$d/run.log; exit 1; }; \
	$(WINE_ENV) bin/zc build tests/fixtures/emitc_corpus/sqlite_hello.z --cc $(MSVC_WINE)/cl --target x86_64-pc-windows-msvc \
	  -o $$d/sqlite_hello.exe > $$d/zcbuild.log 2>&1 \
	  || { echo "test-msvc-wine FAIL: zc build --cc cl did not build sqlite_hello"; tail -10 $$d/zcbuild.log; exit 1; }; \
	$(WINE_ENV) timeout 300 wine $$d/sqlite_hello.exe > $$d/sqlite_hello.out 2>&1; \
	cmp -s $$d/sqlite_hello.out tests/fixtures/run_golden/sqlite_hello.out \
	  || { echo "test-msvc-wine FAIL: zc build --cc cl's sqlite_hello differs from its golden"; diff $$d/sqlite_hello.out tests/fixtures/run_golden/sqlite_hello.out | head; exit 1; }; \
	rc=0; bin/zc build examples/hello.z --cc $(MSVC_WINE)/cl -o $$d/hello.exe > $$d/refuse.log 2>&1 || rc=$$?; \
	{ [ $$rc -eq 2 ] && grep -q 'builds only for Windows' $$d/refuse.log; } \
	  || { echo "test-msvc-wine FAIL: zc build --cc cl for a Linux target did not refuse (exit $$rc)"; cat $$d/refuse.log; exit 1; }; \
	echo "test-msvc-wine OK: MSVC $$($(WINE_ENV) $(MSVC_WINE)/cl 2>&1 | grep -m1 -o 'Version [0-9.]*') built the thread shim's test, which passes under wine; zc build --cc cl builds sqlite_hello (C objects included) to its golden, and refuses a target that is not Windows"

# test-msvc-corpus -- the RUN corpus built for Windows by MSVC itself (cl under
# wine, MSVC_WINE as test-msvc-wine names it; on a Windows host the cl on PATH,
# natively) as a backend (--cc-forward, so units
# cl cannot build are refused by name) and run under wine against the same
# goldens. cl's absolute paths are spelled Z:/... (zcc.msvcPath), since link.exe
# reads a path starting with / as an option. tests/msvc-known-failures.txt is the
# expected failure set -- only the programs cl cannot build at all, refused by
# zc, and the POSIX-only ones -- and a move either way fails. Skipped without
# MSVC_WINE or wine.
MSVC_KNOWN := tests/msvc-known-failures.txt
# On a Windows host it is the same run with the cl on PATH, natively.
ifneq ($(WINDOWS_HOST),)
MSVC_CORPUS_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --cc cl --cc-forward
MSVC_CORPUS_WHERE := natively
else
MSVC_CORPUS_RUN = $(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(MSVC_WINE)/cl --cc-forward \
  --target x86_64-pc-windows-msvc --exec wine
MSVC_CORPUS_WHERE := under wine
endif
test-msvc-corpus: bin/zc $(BUILDDIR)/ztestrunner $(BUILDDIR)/wineprefix/.ready
	@if [ -n "$(WINDOWS_HOST)" ]; then \
	  if ! command -v cl > /dev/null 2>&1; then \
	    echo "test-msvc-corpus: skipped (no cl on PATH: run from a shell set up by MSVC's vcvars64.bat; see tools/windows/README.md)"; exit 0; fi; \
	elif [ -z "$(MSVC_WINE)" ] || [ ! -x "$(MSVC_WINE)/cl" ] || ! command -v wine > /dev/null 2>&1; then \
	  echo "test-msvc-corpus: skipped (needs wine and MSVC_WINE=<dir>/msvc/bin/x64 from tools/windows/msvc-wine.sh; see tools/windows/README.md)"; exit 0; fi; \
	$(MSVC_CORPUS_RUN) --run-only --root . --jobs $(NPROC) > $(BUILDDIR)/msvc-run.log 2>&1; \
	awk '/^FAIL/ || /^SKIP\(run\) .* \(posix\)$$/ { k = $$1; sub(/^FAIL\(/, "", k); sub(/\)$$/, "", k); \
	       print $$2, (NF >= 3 ? $$3 : "(" k ")") }' $(BUILDDIR)/msvc-run.log \
	  | sort > $(BUILDDIR)/msvc-fails.txt; \
	grep -v '^#' $(MSVC_KNOWN) | grep -v '^ *$$' | sort > $(BUILDDIR)/msvc-known.txt; \
	if ! diff -u $(BUILDDIR)/msvc-known.txt $(BUILDDIR)/msvc-fails.txt > $(BUILDDIR)/msvc-diff.txt; then \
	  head -40 $(BUILDDIR)/msvc-diff.txt; \
	  echo "test-msvc-corpus FAIL: the MSVC failure set moved (-known +actual above, all of it in $(BUILDDIR)/msvc-diff.txt)"; \
	  echo "  a gained line is a regression; a lost one passes now -- delete it from"; \
	  echo "  $(MSVC_KNOWN) in the same commit. Full log: $(BUILDDIR)/msvc-run.log"; \
	  exit 1; \
	fi; \
	echo "test-msvc-corpus OK: $$(sed -n 's/^pass=\([0-9]*\).*/\1/p' $(BUILDDIR)/msvc-run.log) RUN cases pass on Windows (MSVC, $(MSVC_CORPUS_WHERE)), $$(grep -c . $(BUILDDIR)/msvc-fails.txt) known failures, none new"

# ci-windows -- the Windows legs on a real Windows machine, reached over ssh as
# WINDOWS_SSH (the zlwin VM by default; any Windows host whose ssh shell is
# MSYS2 bash with make, MSVC's cl and UCRT64 gcc on PATH will do). The run
# corpus is built for Windows here, as test-mingw builds it, keeping the
# binaries in $(WINDOWS_DIR)/build; the tracked tree and those binaries go to a
# fresh directory there, `make windows-guest` runs in it, and its log comes
# back to $(WINDOWS_DIR)/guest.log. Not in ci: it needs the machine. Skipped
# when the host does not answer; tools/windows/winvm.sh builds such a VM on a
# Linux host with KVM (tools/windows/README.md), and WINDOWS_SSH_FLAGS passes
# ssh options (winvm.sh ci names its own ssh config there). A green run records
# the commit it tested in $(WINDOWS_VERIFIED), which windows-due reads.
WINDOWS_SSH ?= zlwin
WINDOWS_SSH_FLAGS ?=
WINDOWS_SSH_CMD = ssh $(WINDOWS_SSH_FLAGS) -o BatchMode=yes -o ConnectTimeout=10 $(WINDOWS_SSH)
WINDOWS_DIR := $(BUILDDIR)/windows
ci-windows: bin/zc $(BUILDDIR)/ztestrunner $(BUILDDIR)/wineprefix/.ready
	@if ! $(WINDOWS_SSH_CMD) true > /dev/null 2>&1; then \
	  echo "ci-windows: skipped (no ssh to $(WINDOWS_SSH); to bring up the zlwin VM, or set up another Windows host, see tools/windows/README.md)"; exit 0; fi; \
	miss=$$($(WINDOWS_SSH_CMD) 'for c in make tar cl gcc; do command -v $$c > /dev/null || printf " %s" $$c; done'); \
	if [ -n "$$miss" ]; then \
	  echo "ci-windows FAIL: $(WINDOWS_SSH) has no$$miss on its ssh PATH -- the guest needs MSYS2 bash as sshd's shell with make, tar, UCRT64 gcc and MSVC's cl (vcvars64) on PATH; see tools/windows/README.md"; exit 1; fi; \
	if ! $(MINGW_UCRT_CHECK); then \
	  echo "ci-windows FAIL: the corpus is built for Windows against the UCRT, which $(MINGW_CC) cannot target: $(MINGW_UCRT_FIX)"; exit 1; fi; \
	rm -rf $(WINDOWS_DIR); mkdir -p $(WINDOWS_DIR); \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS)" --target x86_64-w64-mingw32 \
	   --exec wine --run-only --root . --jobs $(NPROC) --save-temps --tmp $(abspath $(WINDOWS_DIR))/build \
	   > $(WINDOWS_DIR)/build.log 2>&1; \
	dest=/c/zl/run/$$(date +%Y%m%d-%H%M%S)-$(BUILDID); \
	$(WINDOWS_SSH_CMD) "mkdir -p $$dest" || { echo "ci-windows FAIL: cannot create $$dest on $(WINDOWS_SSH)"; exit 1; }; \
	{ git ls-files -co --exclude-standard | while IFS= read -r f; do [ -f "$$f" ] && printf '%s\n' "$$f"; done; \
	  ls $(WINDOWS_DIR)/build/*.bin; } \
	  | tar -cf - -T - | $(WINDOWS_SSH_CMD) "tar -xf - -C $$dest" \
	  || { echo "ci-windows FAIL: copying the tree to $(WINDOWS_SSH)"; exit 1; }; \
	rc=0; $(WINDOWS_SSH_CMD) "cd $$dest && make --no-print-directory windows-guest" > $(WINDOWS_DIR)/guest.log 2>&1 || rc=$$?; \
	$(WINDOWS_SSH_CMD) "cat $$dest/$(WINDOWS_DIR)/guest-fails.txt" > $(WINDOWS_DIR)/guest-fails.txt 2> /dev/null || true; \
	cat $(WINDOWS_DIR)/guest.log; \
	if [ $$rc -ne 0 ]; then echo "ci-windows FAIL on $(WINDOWS_SSH) in $$dest (log $(WINDOWS_DIR)/guest.log)"; exit 1; fi; \
	{ echo "# the last green make ci-windows: commit, unix time, date (written by ci-windows; read by windows-due)"; \
	  echo "$$(git rev-parse HEAD) $$(date +%s) $$(date +%F)"; } > $(WINDOWS_VERIFIED); \
	echo "ci-windows OK on $(WINDOWS_SSH) in $$dest; recorded in $(WINDOWS_VERIFIED) -- commit it"

# windows-guest -- what ci-windows runs on the Windows machine, in the copied
# tree. The thread shim's test is built and run under MSVC (cl) and UCRT64
# gcc. Then every run-corpus binary ci-windows built is run natively, in a
# directory of its own, against its golden's stdout and exit code (Windows' own
# `<name>.windows.out` / `.exit` where the case has one); the failure
# set must equal $(MINGW_KNOWN), what fails under wine, and the report names
# each program on which wine and Windows disagree. os_unicode_text runs once
# more on a console of its own, which tests/c/zconsole_read.c reads back: ssh
# gives every program pipes. Then the tools and tcc are built natively (MSYS2
# make, UCRT64 gcc). zc and zl, given a file by its drive path from another
# directory, must name it and the system unit natively, and zl must find the
# file's project. Last, `make ci` runs with the native tools: every gate on
# Windows itself, the Linux-only ones saying so. cl's flags are spelled
# -flag: MSYS2 rewrites a /flag argument as a path.
windows-guest: $(NATIVEC_INC)/zthread_shim.inc
	@d=$(WINDOWS_DIR); fail=0; t=tests/c/zthread_shim_test.c; \
	cl -nologo -std:c17 -experimental:c11atomics -W3 -O2 -I$(NATIVEC_INC) $$t -Fe:$$d/shim-msvc.exe -Fo:$$d/ > $$d/cl.log 2>&1 \
	  || { echo "windows-guest FAIL: cl did not build the shim test"; tail -5 $$d/cl.log; fail=1; }; \
	gcc -std=c17 -Wall -Wextra -Wno-unused-function -O2 -I$(NATIVEC_INC) -o $$d/shim-gcc.exe $$t > $$d/gcc.log 2>&1 \
	  || { echo "windows-guest FAIL: UCRT64 gcc did not build the shim test"; tail -5 $$d/gcc.log; fail=1; }; \
	for v in msvc gcc; do \
	  [ -f $$d/shim-$$v.exe ] || continue; \
	  timeout 300 $$d/shim-$$v.exe > $$d/shim-$$v.log 2>&1 && grep -q "zthread shim OK" $$d/shim-$$v.log \
	    || { echo "windows-guest FAIL: the shim test under $$v"; tail -10 $$d/shim-$$v.log; fail=1; }; \
	done; \
	n=0; pass=0; : > $$d/guest-fails.txt; nl=$$(printf '\nx'); nl=$${nl%x}; \
	while read -r name dir rest; do \
	  [ -n "$$name" ] || continue; \
	  if [ "$$rest" = posix ]; then echo "$$name (posix)" >> $$d/guest-fails.txt; continue; fi; \
	  g=tests/fixtures/run_golden/$$name; \
	  if [ ! -f $$d/build/$$name.bin ]; then echo "$$name (cc)" >> $$d/guest-fails.txt; continue; fi; \
	  [ -f $$g.out ] || continue; \
	  go=$$g.out; [ -f $$g.windows.out ] && go=$$g.windows.out; \
	  ge=$$g.exit; [ -f $$g.windows.exit ] && ge=$$g.windows.exit; \
	  n=$$((n + 1)); rd=$$d/build/run_$$name; mkdir -p $$rd; cp $$d/build/$$name.bin $$rd/$$name.exe; \
	  set -f; IFS="$$nl"; set -- $$( [ -f $$g.args ] && grep -v '^$$' $$g.args ); unset IFS; set +f; \
	  (cd $$rd && timeout 60 ./$$name.exe "$$@" < /dev/null > $$name.out 2> /dev/null); rc=$$?; \
	  want=0; [ -f $$ge ] && want=$$(tr -d ' \r\n' < $$ge); \
	  if cmp -s $$rd/$$name.out $$go && [ "$$rc" = "$$want" ]; then pass=$$((pass + 1)); \
	  else echo "$$name (run)" >> $$d/guest-fails.txt; fi; \
	done < tests/fixtures/run_cases.txt; \
	sort -o $$d/guest-fails.txt $$d/guest-fails.txt; \
	grep -v '^#' $(MINGW_KNOWN) | grep -v '^ *$$' | sort > $$d/wine-fails.txt; \
	if ! cmp -s $$d/wine-fails.txt $$d/guest-fails.txt; then \
	  comm -13 $$d/wine-fails.txt $$d/guest-fails.txt | sed 's/^/  fails on Windows, not under wine: /'; \
	  comm -23 $$d/wine-fails.txt $$d/guest-fails.txt | sed 's/^/  passes on Windows, not under wine: /'; \
	  echo "windows-guest FAIL: the Windows failure set differs from $(MINGW_KNOWN)"; fail=1; \
	fi; \
	g=tests/fixtures/run_golden/os_unicode_text; c=tests/c/zconsole_read.c; set -f; IFS="$$nl"; set -- $$(grep -v '^$$' $$g.args); unset IFS; set +f; \
	{ gcc -std=c17 -Wall -Wextra -O2 -o $$d/zconsole_read.exe $$c -lshell32 \
	  && $$d/zconsole_read.exe "$$(cygpath -w $$g.out)" "$$(cygpath -w $$d/build/run_os_unicode_text/os_unicode_text.exe)" "$$@"; } \
	  > $$d/console.log 2>&1 \
	  || { echo "windows-guest FAIL: os_unicode_text on a console does not show its golden"; cat $$d/console.log; fail=1; }; \
	ci=""; \
	if $(MAKE) --no-print-directory zc bin/zl bin/zls $(BUILDDIR)/ztestrunner tcc > $$d/native-build.log 2>&1; then \
	  r=$$PWD; w=$$(cygpath -w "$$r"); \
	  (cd $$d && "$$r/bin/zc" build "$$w\\tests\\fixtures\\errors\\display_path_cleaned.z" -o drive.exe) > $$d/drive-zc.log 2>&1; \
	  grep -qxF " --> $$w\\tests\\fixtures\\errors\\display_path_cleaned.z:5:23" $$d/drive-zc.log \
	    && grep -qxF "     = note: unit 'io' is $$w\\lib\\system\\io.z" $$d/drive-zc.log \
	    || { echo "windows-guest FAIL: zc given a drive path does not name the files natively"; cat $$d/drive-zc.log; fail=1; }; \
	  (cd $$d && "$$r/bin/zl" lint --full "$$w\\src\\zsource.z") > $$d/drive-zl.log 2>&1 \
	    && grep -qF " --> $$w\\src\\zsource.z:" $$d/drive-zl.log \
	    || { echo "windows-guest FAIL: zl lint given a drive path does not find its project"; grep -m5 -E '^(error|warning)' $$d/drive-zl.log; fail=1; }; \
	  { "$$r/bin/zc" build tests/fixtures/emitc_corpus/sqlite_hello.z --cc cl -o $$d/sqlite-cl.exe \
	    && $$d/sqlite-cl.exe > $$d/sqlite-cl.out && cmp -s $$d/sqlite-cl.out tests/fixtures/run_golden/sqlite_hello.out; } > $$d/zc-cl.log 2>&1 \
	    || { echo "windows-guest FAIL: zc build --cc cl does not build sqlite_hello to its golden"; tail -10 $$d/zc-cl.log; fail=1; }; \
	  $(MAKE) --no-print-directory ci > $$d/native-ci.log 2>&1 \
	    || { echo "windows-guest FAIL: make ci with the native tools"; grep -E 'FAIL|Error [0-9]' $$d/native-ci.log | head -20; fail=1; }; \
	  ci="$$(grep -c 'skipped (Linux only' $$d/native-ci.log) Linux-only legs skipped; $$(grep -m1 -o 'agreement OK: [0-9]* toolchain(s)' $$d/native-ci.log) agree in the bootstrap; $$(grep -m1 -o '[0-9]* RUN cases pass on Windows (MSVC, natively)' $$d/native-ci.log)"; \
	else echo "windows-guest FAIL: zc, zl, zls, the runner and tcc did not build natively"; tail -10 $$d/native-build.log; fail=1; fi; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "windows-guest OK: the thread shim under MSVC and UCRT64 gcc; $$pass of $$n run-corpus binaries match their goldens on Windows, the rest as under wine; non-ASCII text on a console; drive paths; zc build --cc cl; make ci with the native tools ($$ci)"

# windows-due -- one line saying make ci-windows is due, nothing when it is not:
# it has never passed, or its last green run ($(WINDOWS_VERIFIED)) is
# WINDOWS_DUE_DAYS days old, or WINDOWS_DUE_TOUCHED_DAYS days old with a commit
# since then touching WINDOWS_PATHS, the sources Windows behaviour depends on.
# Never fails: ci ends with it, as a reminder rather than a gate, since ci runs
# where no Windows host may be.
WINDOWS_VERIFIED := tests/windows-verified.txt
WINDOWS_DUE_DAYS := 14
WINDOWS_DUE_TOUCHED_DAYS := 2
WINDOWS_PATHS := src/zemitterc.z src/zc.z src/zcobj.z src/zcheck.z src/ztestrunner.z \
	lib/system bootstrap/zc.c Makefile tests/c
windows-due:
	@if [ ! -f $(WINDOWS_VERIFIED) ]; then echo "windows-due: make ci-windows has never passed (tools/windows/README.md)"; exit 0; fi; \
	set -- $$(grep -v '^#' $(WINDOWS_VERIFIED)); sha=$$1; at=$$2; day=$$3; \
	age=$$(( ($$(date +%s) - at) / 86400 )); \
	touched=$$(git log --oneline $$sha..HEAD -- $(WINDOWS_PATHS) 2> /dev/null | wc -l); \
	if [ $$age -ge $(WINDOWS_DUE_DAYS) ]; then \
	  echo "windows-due: make ci-windows last passed $$age days ago ($$day, $$(echo $$sha | cut -c1-8)) -- run it (tools/windows/README.md)"; \
	elif [ $$age -ge $(WINDOWS_DUE_TOUCHED_DAYS) ] && [ $$touched -gt 0 ]; then \
	  echo "windows-due: $$touched commit(s) touching Windows-relevant sources since make ci-windows last passed ($$day, $$(echo $$sha | cut -c1-8)) -- run it (tools/windows/README.md)"; \
	fi

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

# math-asm-guard -- the committed asm kernels (MATH_ASM) are what tools/asmgen
# writes.
MATH_ASM := lib/system/math/nativec/asm.zd
MATH_ASM_CHECK := $(BUILDDIR)/asmgen-check/asm.zd
math-asm-guard: out/asmgen
	@mkdir -p $(dir $(MATH_ASM_CHECK))
	@$(BUILDDIR)/asmgen $(MATH_ASM_CHECK)
	@if ! cmp -s $(MATH_ASM_CHECK) $(MATH_ASM); then \
	  echo "math-asm-guard FAIL: $(MATH_ASM) is not what tools/asmgen writes -- make regen-math-asm"; \
	  diff $(MATH_ASM_CHECK) $(MATH_ASM) | head -20; exit 1; fi
	@echo "math-asm-guard OK: $(MATH_ASM) is tools/asmgen's"

# bench-math -- math's operations timed per kernel tier (asm, builtins, portable,
# tcc). Not in ci: wall time is no ratchet.
BENCH_MATH_DIR := $(BUILDDIR)/bench-math
# the tcc tier needs the vendored tcc, which only an x86_64 host builds.
BENCH_TCC := $(if $(filter x86_64,$(HOST_ARCH)),tcc)
BENCH_TIERS := asm builtins portable $(BENCH_TCC)
bench-math: bin/zc $(if $(BENCH_TCC),$(BUILDDIR)/tcc) bench-math-kernels
	@mkdir -p $(BENCH_MATH_DIR)
	@bin/zc build tests/bench/math_bench.z --release -o $(BENCH_MATH_DIR)/asm
	@bin/zc build tests/bench/math_bench.z --release --cflags -DZ_MATH_NOASM -o $(BENCH_MATH_DIR)/builtins
	@bin/zc build tests/bench/math_bench.z --release --cflags -DZ_WORD_PORTABLE -o $(BENCH_MATH_DIR)/portable
	@$(if $(BENCH_TCC),bin/zc build tests/bench/math_bench.z --cc $(BUILDDIR)/tcc --tcc-lib $(TCCLIB) -o $(BENCH_MATH_DIR)/tcc)
	@pin=""; if command -v taskset >/dev/null 2>&1; then pin="taskset -c $(BENCH_CPU)"; fi; \
	for v in $(BENCH_TIERS); do echo "== $$v"; $$pin $(BENCH_MATH_DIR)/$$v; done

# bench-math-kernels -- the word-vector kernels timed alone, per tier; the median of
# REPS runs, pinned to BENCH_CPU (the host mixes two kinds of core). BENCH_WORDS
# (N,N,...) times those lengths in place of Go's series.
REPS ?= 5
BENCH_CPU ?= 0
BENCH_WORDS ?=
bench-math-kernels: bin/zc $(if $(BENCH_TCC),$(BUILDDIR)/tcc)
	@mkdir -p $(BENCH_MATH_DIR)
	@{ bin/zc natives show hash.mul128 --c --system lib/system; \
	   bin/zc natives show head.clz64 --c --system lib/system; } > $(BENCH_MATH_DIR)/mul128.h
	@bin/zc natives show system.@word --c --system lib/system > $(BENCH_MATH_DIR)/system_word.inc
	@bin/zc natives show math.@arith --c --system lib/system > $(BENCH_MATH_DIR)/math_arith.inc
	@inc="-I$(BENCH_MATH_DIR)"; \
	$(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function $$inc -o $(BENCH_MATH_DIR)/k-asm tests/bench/math_kernels.c \
	&& $(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function -DZ_MATH_NOASM $$inc -o $(BENCH_MATH_DIR)/k-builtins tests/bench/math_kernels.c \
	&& $(CC) -O2 -std=c17 -Wall -Wextra -Wno-unused-function -DZ_WORD_PORTABLE $$inc -o $(BENCH_MATH_DIR)/k-portable tests/bench/math_kernels.c \
	$(if $(BENCH_TCC),&& $(BUILDDIR)/tcc -B $(TCCLIB) $$inc -o $(BENCH_MATH_DIR)/k-tcc tests/bench/math_kernels.c)
	@pin=""; if command -v taskset >/dev/null 2>&1; then pin="taskset -c $(BENCH_CPU)"; fi; \
	for v in $(BENCH_TIERS); do echo "== kernels $$v"; $$pin $(BENCH_MATH_DIR)/k-$$v -r $(REPS) $(if $(BENCH_WORDS),-n $(BENCH_WORDS)); done

# test-clang -- the corpus under clang. Only clang shows an f128 helper the
# prelude forgot to declare (gcc reads quadmath.h).
test-clang: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc clang --cc-forward --root . --jobs $(NPROC)

# test-signed-ub -- the run corpus with C's undefined signed arithmetic trapping:
# signed overflow and out-of-range shifts under UBSan, without -fwrapv. The
# language promises two's-complement wrapping, and MSVC has no -fwrapv, so the
# emitted C and the runtime must never reach that undefined behaviour; a report
# aborts its case. Linux only: the Windows toolchains have no UBSan runtime.
SIGNED_UB_FLAGS := -fsanitize=signed-integer-overflow,shift -fno-sanitize-recover=all -fno-wrapv
ifneq ($(WINDOWS_HOST),)
test-signed-ub:
	@echo "test-signed-ub: skipped (Linux only: UCRT64 gcc has no UBSan runtime; the Linux run covers the same C)"
else
test-signed-ub: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc gcc --ccflags "$(SIGNED_UB_FLAGS)" --run-only --root . \
	   --jobs $(NPROC) > $(BUILDDIR)/signed-ub.log 2>&1 \
	  || { grep -E '^FAIL|runtime error' $(BUILDDIR)/signed-ub.log | head -20; \
	       echo "test-signed-ub FAIL: a run case reached C's undefined signed arithmetic (UBSan; full log $(BUILDDIR)/signed-ub.log)"; exit 1; }; \
	tail -2 $(BUILDDIR)/signed-ub.log | grep -q 'CORPUS GATE GREEN' \
	  || { tail -5 $(BUILDDIR)/signed-ub.log; echo "test-signed-ub FAIL: the run did not finish green"; exit 1; }; \
	echo "test-signed-ub OK: $$(grep -o 'pass=[0-9]*' $(BUILDDIR)/signed-ub.log | tail -1) run cases, no signed overflow or out-of-range shift"
endif

# what a corpus run under the vendored tcc needs built first.
TCC_RUN_DEPS := bin/zc bin/zl $(BUILDDIR)/tcc $(BUILDDIR)/ztestrunner

# test-tcc -- the corpus under the vendored tcc, as a backend (--cc-forward), so
# units tcc cannot build are refused by name. tests/tcc-known-failures.txt is
# the expected failure set; a move either way fails. On Windows (the native tcc,
# in the VM's windows-guest) it is the Windows list test-tcc-mingw keeps, whose
# `(posix)` cases the runner lists rather than runs.
TCC_KNOWN := $(if $(WINDOWS_HOST),tests/tcc-mingw-known-failures.txt,tests/tcc-known-failures.txt)
# empty for the fast tier; test-tcc-heavy overrides it.
TCC_TIER ?=

test-tcc: $(TCC_RUN_DEPS)
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(BUILDDIR)/tcc$(EXE) --cc-forward \
	   --ccflags "-B $(TCCLIB)" --root . $(TCC_TIER) --jobs $(NPROC) \
	   > $(BUILDDIR)/tcc-run.log 2>&1; \
	awk '/^FAIL/ || /^SKIP\(run\) .* \(posix\)$$/ { k = $$1; sub(/^FAIL\(/, "", k); sub(/\)$$/, "", k); \
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
# C-keyword members, locals named as system-header macros). A readable-named
# compiler must emit identical C, and with mingw-w64 present the readable C of
# the macro case and of the compiler must compile for Windows, whose headers
# define macros Linux's do not (`near`, `far`, `FAR`).
readable-check: bin/zc $(BUILDDIR)/buildstamp.o
	@mkdir -p $(BUILDDIR)/rn
	@for c in hello:examples vector:examples records:examples fibonacci:examples \
	          typedefs:examples shadow_unit_const:tests/fixtures/emitc_corpus \
	          rn_sibling_shadow:tests/fixtures/emitc_corpus rn_macro_names:tests/fixtures/emitc_corpus \
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
	$(CC) $(CFLAGS) $(STAMPDEF) -c $(BUILDDIR)/rn/zc-rn.c -o $(BUILDDIR)/rn/zc-rn.o
	$(CC) $(DRIVERLINK) -o $(BUILDDIR)/rn/zc-rn $(BUILDDIR)/rn/zc-rn.o $(BUILDDIR)/buildstamp.o \
	  $(call ZLINKOF,$(BUILDDIR)/rn/zc-rn.c) $(THREADLIB) $(LIBM)
	@$(BUILDDIR)/rn/zc-rn hello --src examples --system lib/system --emit-c $(BUILDDIR)/rn/hello-by-rn.c
	@bin/zc hello --src examples --system lib/system --emit-c $(BUILDDIR)/rn/hello-by-id.c
	@cmp $(BUILDDIR)/rn/hello-by-id.c $(BUILDDIR)/rn/hello-by-rn.c \
	  || { echo "readable-check FAIL: the readable-named compiler emits different C"; exit 1; }
	@if $(MINGW_UCRT_CHECK); then \
	  for c in rn_macro_names:tests/fixtures/emitc_corpus zc:src; do \
	    n=$${c%%:*}; d=$${c#*:}; \
	    bin/zc $$n --src $$d --system lib/system --readable-names --target x86_64-w64-mingw32 \
	      --emit-c $(BUILDDIR)/rn/$$n-win.c || exit 1; \
	    $(MINGW_CC) -std=c17 -fsyntax-only $(MINGW_CFLAGS) $(BUILDDIR)/rn/$$n-win.c 2> $(BUILDDIR)/rn/$$n-win.log \
	      || { echo "readable-check FAIL: $$n's readable C does not compile for Windows"; grep -m3 error $(BUILDDIR)/rn/$$n-win.log; exit 1; }; \
	  done; \
	fi
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
TCC_TRIPLE ?= $(if $(WINDOWS_HOST),windows-x86_64,linux-x86_64)
TCC_CONFIGDIR := vendor/tinycc/config/$(TCC_TRIPLE)
TCC_SRC := $(wildcard vendor/tinycc/src/*.c vendor/tinycc/src/*.h \
                      vendor/tinycc/src/Makefile vendor/tinycc/src/lib/* \
                      vendor/tinycc/src/include/*)
# the Windows (PE) sources: the cross build's, and the native build's on Windows
TCC_WIN_SRC := $(wildcard vendor/tinycc/src/win32/lib/* vendor/tinycc/src/win32/include/*.h \
                          vendor/tinycc/src/win32/include/*/*.h)
TCC_MAKE = $(MAKE) -C $(BUILDDIR)/tinycc -j1 GITHASH=no CPPFLAGS=-fPIC
TCCLIB := $(BUILDDIR)/tcc-lib
# On Windows (MSYS2 make, UCRT64 gcc) the same tree builds tcc.exe, which loads
# libtcc.dll (LGPL: a DLL, never linked in), and the payload is laid out as a
# Windows tcc reads it: include/ (win32/include, for the UCRT -- see
# vendor/tinycc/VERSION.md -- and tcc's own headers), lib/ (libtcc1.a and the
# .def import lists, generated by the built tcc's -impdef from System32; none
# is vendored) and libtcc.dll. tcc.exe finds libtcc.dll beside it.
LIBTCC := $(if $(WINDOWS_HOST),libtcc.dll,libtcc.so)
TCC_WIN_DLLS := kernel32 advapi32 shell32 ws2_32 ucrtbase user32 gdi32

$(BUILDDIR)/tcc: $(TCC_SRC) $(if $(WINDOWS_HOST),$(TCC_WIN_SRC)) $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak
	@if [ "$(HOST_ARCH)" != x86_64 ]; then \
	  echo "$@: the vendored tinycc is an x86_64 build; this host is $(HOST_ARCH)"; exit 1; fi
	@mkdir -p $(BUILDDIR)
	rm -rf $(BUILDDIR)/tinycc $(TCCLIB)
	mkdir -p $(BUILDDIR)/tinycc $(TCCLIB)
	cp -r vendor/tinycc/src/. $(BUILDDIR)/tinycc/
	cp $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak $(BUILDDIR)/tinycc/
ifdef WINDOWS_HOST
	$(MAKE) -C $(BUILDDIR)/tinycc -j1 GITHASH=no tcc.exe
	$(MAKE) -C $(BUILDDIR)/tinycc -j1 GITHASH=no libtcc1.a
	mkdir -p $(TCCLIB)/lib
	cp $(BUILDDIR)/tinycc/libtcc1.a $(TCCLIB)/lib/
	cp $(BUILDDIR)/tinycc/libtcc.dll $(TCCLIB)/
	cp -r vendor/tinycc/src/win32/include $(TCCLIB)/include
	cp vendor/tinycc/src/include/*.h $(TCCLIB)/include/
	for d in $(TCC_WIN_DLLS); do \
	  $(BUILDDIR)/tinycc/tcc.exe -impdef "$$SYSTEMROOT/System32/$$d.dll" -o $(TCCLIB)/lib/$$d.def || exit 1; done
	cp $(BUILDDIR)/tinycc/libtcc.dll $(BUILDDIR)/
	cp $(BUILDDIR)/tinycc/tcc.exe $@.exe
else
	$(TCC_MAKE) tcc
	$(TCC_MAKE) libtcc.so
	$(TCC_MAKE) libtcc1.a
	cp $(BUILDDIR)/tinycc/libtcc1.a $(BUILDDIR)/tinycc/libtcc.so $(TCCLIB)/
	cp $(BUILDDIR)/tinycc/runmain.o $(BUILDDIR)/tinycc/bt-exe.o \
	   $(BUILDDIR)/tinycc/bt-log.o $(BUILDDIR)/tinycc/bcheck.o $(TCCLIB)/
	cp -r vendor/tinycc/src/include $(TCCLIB)/include
	cp $(BUILDDIR)/tinycc/tcc $@
endif

$(TCCLIB)/$(LIBTCC): $(BUILDDIR)/tcc ;

# out/x86_64-win32-tcc + out/tcc-win32-lib -- the vendored tinycc as a
# Linux-hosted cross compiler for Windows (upstream's `make cross-x86_64-win32`
# from a staged copy, with this host's config), and its payload in the layout a
# Windows tcc reads: include/ (win32/include, which targets the UCRT -- see
# vendor/tinycc/VERSION.md -- and tcc's own headers) and lib/ (libtcc1.a and
# the .def import lists). The import lists are generated by the cross tcc's
# -impdef from the DLLs of the wine prefix the Windows legs share, as a Windows
# build generates them from System32; none is vendored.
TCCW_LIB := $(BUILDDIR)/tcc-win32-lib
TCCW_DLLS := kernel32 advapi32 shell32 ws2_32 ucrtbase user32 gdi32
$(BUILDDIR)/x86_64-win32-tcc: $(TCC_SRC) $(TCC_WIN_SRC) $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak
	@test -f $(WINE_PREFIX)/drive_c/windows/system32/ucrtbase.dll \
	  || { echo "$@: needs the wine prefix (make $(BUILDDIR)/wineprefix/.ready) for its import lists"; exit 1; }
	rm -rf $(BUILDDIR)/tinycc-win32 $(TCCW_LIB)
	mkdir -p $(BUILDDIR)/tinycc-win32 $(TCCW_LIB)/lib
	cp -r vendor/tinycc/src/. $(BUILDDIR)/tinycc-win32/
	cp $(TCC_CONFIGDIR)/config.h $(TCC_CONFIGDIR)/config.mak $(BUILDDIR)/tinycc-win32/
	$(MAKE) -C $(BUILDDIR)/tinycc-win32 -j1 GITHASH=no cross-x86_64-win32
	cp -r vendor/tinycc/src/win32/include $(TCCW_LIB)/include
	cp vendor/tinycc/src/include/*.h $(TCCW_LIB)/include/
	cp $(BUILDDIR)/tinycc-win32/x86_64-win32-libtcc1.a $(TCCW_LIB)/lib/
	for d in $(TCCW_DLLS); do \
	  $(BUILDDIR)/tinycc-win32/x86_64-win32-tcc -impdef $(WINE_PREFIX)/drive_c/windows/system32/$$d.dll \
	    -o $(TCCW_LIB)/lib/$$d.def || exit 1; done
	cp $(BUILDDIR)/tinycc-win32/x86_64-win32-tcc $@

tcc: $(BUILDDIR)/tcc $(TCCLIB)/$(LIBTCC)
	@echo "vendored tcc: $(BUILDDIR)/tcc$(EXE) (payload $(TCCLIB))"

# out/zc-seed -- the bootstrap compiler, from the committed seed. The seed is
# emitted for Linux, so its zlink line is Linux's (dl, for the in-process tcc);
# on Windows it links no library -- there is no in-process tcc there.
$(BUILDDIR)/zc-seed: bootstrap/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c $(if $(WINDOWS_HOST),$(DRIVERLINK),$(call ZLINKOF,bootstrap/zc.c)) -lm

# NATIVEC_DEP -- the files the generated `nativec` unit is made from (znativec.servedText):
# zc serves them to its own build, so a change to one rebuilds bin/zc
NATIVEC_DEP := lib/system/runtime.zd $(wildcard lib/system/*/nativec.zd lib/system/*/nativec/*.zd)

# the C of rows zc carries, written out for a C test to include (NATIVEC_INC)
$(NATIVEC_INC)/zthread_shim.inc: bin/zc
	@mkdir -p $(NATIVEC_INC)
	bin/zc natives show prelude.@zthread --c --system lib/system > $@

# bin/zc -- the self-hosted compiler, built by the seed.
bin/zc.c: $(wildcard src/*.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) $(NATIVEC_DEP) $(ZC)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c

$(BUILDDIR)/zc.o: bin/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) $(OPTFLAGS) $(STAMPDEF) -c bin/zc.c -o $@

bin/zc: $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) $(DRIVERLINK) -o bin/zc $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,bin/zc.c) $(THREADLIB) -lm

zc: bin/zc

# bin/zl -- the linter and formatter (src/zl.z); no emitter.
out/zl.c: $(BUILDDIR)/zc.o $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) | bin/zc
	@mkdir -p out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c

$(BUILDDIR)/zl.o: out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) $(STAMPDEF) -c out/zl.c -o $@

bin/zl: $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) $(DRIVERLINK) -o bin/zl $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zl.c) $(THREADLIB) -lm

# bin/zls -- the language server (src/zls.z); no emitter.
out/zls.c: $(BUILDDIR)/zc.o $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) | bin/zc
	@mkdir -p out
	bin/zc zls --src src --system lib/system $(ZCHASH) --emit-c out/zls.c

$(BUILDDIR)/zls.o: out/zls.c
	$(CC) $(CFLAGS) $(OPTFLAGS) $(STAMPDEF) -c out/zls.c -o $@

bin/zls: $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) $(DRIVERLINK) -o bin/zls $(BUILDDIR)/zls.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zls.c) $(THREADLIB) -lm

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

# The constant-parity matrix generator: every kind of unit-level constant by
# every reader, each read as a unit constant, in a body and on a local.
out/constparitygen: bin/zc tests/unit/constparitygen.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc constparitygen --src tests/unit --system lib/system --emit-c $(BUILDDIR)/constparitygen.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/constparitygen $(BUILDDIR)/constparitygen.c $(call ZLINKOF,$(BUILDDIR)/constparitygen.c) -lm

# regen-constparity -- rewrite the cpar_ fixtures from tests/fixtures/constparity_expect.txt.
regen-constparity: out/constparitygen
	$(BUILDDIR)/constparitygen --root .

# constparity-guard -- the committed cpar_ fixtures are constparitygen's output,
# regenerated under a scratch root, with none left over that it no longer writes.
constparity-guard: out/constparitygen
	@d=$$(mktemp -d); mkdir -p $$d/tests/fixtures; \
	cp tests/fixtures/constparity_expect.txt tests/fixtures/run_cases.txt $$d/tests/fixtures/; \
	$(BUILDDIR)/constparitygen --root $$d > $$d/gen.log 2>&1 || { cat $$d/gen.log; rm -rf $$d; exit 1; }; \
	bad=0; n=0; \
	for f in $$(cd $$d && find tests/fixtures -type f ! -name constparity_expect.txt); do \
	  n=$$((n+1)); \
	  if ! cmp -s $$d/$$f $$f; then echo "constparity-guard FAIL: $$f differs from constparitygen's output"; bad=1; fi; \
	done; \
	for f in $$(find tests/fixtures/emitc_corpus tests/fixtures/run_golden tests/fixtures/errors -name 'cpar_*' -type f); do \
	  if [ ! -f $$d/$$f ]; then echo "constparity-guard FAIL: $$f is no longer generated"; bad=1; fi; \
	done; \
	rm -rf $$d; \
	if [ $$bad -ne 0 ]; then echo "  run 'make regen-constparity' and review the diff"; exit 1; fi; \
	echo "constparity-guard OK: $$n files equal constparitygen's output"

# tools/asmgen -- writes math's word-vector kernels as inline asm, per architecture.
out/asmgen: bin/zc $(wildcard tools/asmgen/*.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc asmgen --src tools/asmgen --system lib/system --emit-c $(BUILDDIR)/asmgen.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/asmgen $(BUILDDIR)/asmgen.c $(call ZLINKOF,$(BUILDDIR)/asmgen.c) -lm

regen-math-asm: out/asmgen
	$(BUILDDIR)/asmgen $(MATH_ASM)

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
# in ci, and MSVC's cl too on a Windows host. A named compiler that is missing
# fails rather than proving less.
BOOTSTRAP_CCS ?= $(CC)

CI_BOOTSTRAP_CCS ?= $(CC) clang $(BUILDDIR)/tcc $(if $(WINDOWS_HOST),cl)

# test-bootstrap -- per compiler: cc the seed, bootstrap twice and require the
# fixpoint (b2 == b3), and check a seed-built zc compiles the ztypes smoke to its
# golden. Then across compilers: b1 must be byte-identical whichever compiler
# built the zc that emitted it. tcc gets -B and none of the -Werror pins; cl
# its own spellings (-Fe: for the executable, its objects into $(BUILDDIR),
# `<name>.lib`) and its default static C runtime. On Windows each build links
# as the drivers do (DRIVERLINK; tcc cannot link statically), and the seed
# without its zlink set, which is the one of the system it was emitted on.
test-bootstrap:
	@mkdir -p $(BUILDDIR)
	@set -e; \
	for cc in $(BOOTSTRAP_CCS); do \
	  tag=$$(basename $$cc); m=""; o="-o "; \
	  case "$$cc" in *tcc*) f="-B $(TCCLIB)"; l="";; \
	    cl|cl.exe|*/cl|*/cl.exe) f="$(MSVC_CFLAGS) -O2 -Fo:$(BUILDDIR)/"; l=""; m=1; o="-Fe:";; \
	    *) f="$(CFLAGS)"; l="$(DRIVERLINK)";; esac; \
	  if ! command -v $$cc >/dev/null 2>&1 && [ ! -x $$cc ]; then \
	    echo "test-bootstrap FAIL: '$$cc' is not available"; \
	    echo "  BOOTSTRAP_CCS names the toolchains this claim is proved against, so a"; \
	    echo "  missing one is a smaller claim, not a smaller run. Install it, or narrow"; \
	    echo "  the list deliberately: make test-bootstrap BOOTSTRAP_CCS='gcc'"; \
	    exit 1; \
	  fi; \
	  echo "== bootstrap chain: $$cc =="; \
	  $$cc $$f $${o}$(BUILDDIR)/zc-seed-$$tag bootstrap/zc.c $(if $(WINDOWS_HOST),$$l,$(call ZLINKOF,bootstrap/zc.c)) $(LIBM); \
	  $(BUILDDIR)/zc-seed-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b1-$$tag.c; \
	  if [ -n "$$m" ]; then k="$(call MSVCLINKOF,$(BUILDDIR)/b1-$$tag.c)"; else k="$(call ZLINKOF,$(BUILDDIR)/b1-$$tag.c) $(LIBM)"; fi; \
	  $$cc $$f $$l $${o}$(BUILDDIR)/zc-b1-$$tag $(BUILDDIR)/b1-$$tag.c $$k; \
	  $(BUILDDIR)/zc-b1-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b2-$$tag.c; \
	  $$cc $$f $$l $${o}$(BUILDDIR)/zc-b2-$$tag $(BUILDDIR)/b2-$$tag.c $$k; \
	  $(BUILDDIR)/zc-b2-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b3-$$tag.c; \
	  if cmp -s $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c; then \
	    echo "  fixpoint OK (b2 == b3) under $$tag"; \
	  else \
	    echo "FAIL: seed-built compiler does not converge under $$tag"; \
	    diff $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c | head -6; exit 1; \
	  fi; \
	  $(BUILDDIR)/zc-b1-$$tag ztypes_smoke --src tests/unit --src src --system lib/system --emit-c $(BUILDDIR)/zt-$$tag.c; \
	  if [ -n "$$m" ]; then k="$(call MSVCLINKOF,$(BUILDDIR)/zt-$$tag.c)"; else k="$(call ZLINKOF,$(BUILDDIR)/zt-$$tag.c) $(LIBM)"; fi; \
	  $$cc $$f $$l $${o}$(BUILDDIR)/zt-$$tag $(BUILDDIR)/zt-$$tag.c $$k; \
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
	rm -rf $(ROOT)/lib/system $(ROOT)/lib/tcc $(ROOT)/lib/vendor $(ROOT)/docs $(ROOT)/src
	cp -r lib/system $(ROOT)/lib/system
	mkdir -p $(ROOT)/lib/vendor
	cp -r vendor/sqlite $(ROOT)/lib/vendor/sqlite
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

# pedantic-check -- how far the emitted C is from ISO C17, which is all MSVC
# compiles: gcc -Wpedantic's diagnostics over the C of zc, zl and zls and over
# the C of every RUN case, emitted for Linux and for Windows, each total held in
# tests/pedantic-baseline.txt. Each target's C is read by a gcc FOR that target,
# since the runtime chooses its branches by the compiler's own _WIN32: on Linux
# the host gcc and the mingw-w64 cross gcc, on a Windows host UCRT64 gcc, where
# no compiler for Linux exists and that half says so and is skipped.
# A total that moves either way fails: a rise is C that only some compilers
# take, and a fall is recorded by lowering the baseline in the same commit.
# -Wpedantic does not see __typeof__, __builtin_* or __attribute__;
# test-msvc-corpus, cl itself, does.
PEDANTIC_DIR := $(BUILDDIR)/pedantic
PEDANTIC_BASELINE := tests/pedantic-baseline.txt
PEDANTIC_FLAGS := -std=c17 -Wpedantic -fsyntax-only
ifeq ($(WINDOWS_HOST),)
PEDANTIC_TARGETS := linux:x86_64-linux-gnu windows:x86_64-w64-mingw32
PEDANTIC_CC_linux := gcc
PEDANTIC_CC_windows := $(MINGW_CC) $(MINGW_CFLAGS)
else
PEDANTIC_TARGETS := windows:x86_64-w64-mingw32
PEDANTIC_CC_windows := gcc
endif
pedantic-check: bin/zc
	@if [ -z "$(WINDOWS_HOST)" ] && ! $(MINGW_UCRT_CHECK); then \
	  echo "pedantic-check FAIL: the Windows C is read by $(MINGW_CC), which cannot target the UCRT: $(MINGW_UCRT_FIX); see tools/windows/README.md"; exit 1; fi; \
	if [ -n "$(WINDOWS_HOST)" ]; then echo "pedantic-check: drivers-linux and corpus-linux skipped (Linux only: no compiler for Linux on Windows)"; fi; \
	rm -rf $(PEDANTIC_DIR); mkdir -p $(PEDANTIC_DIR); \
	one() { cc=$$1; o=$$2; n=$$3; t=$$4; dirs=$$5; shift 5; \
	  for d in $$(echo "$$dirs" | tr ":" " "); do set -- "$$@" --src "$$d"; done; \
	  if bin/zc "$$n" "$$@" --system lib/system --target "$$t" --emit-c "$$o/$$n.c" > "$$o/$$n.zc" 2>&1; then \
	    $$cc $(PEDANTIC_FLAGS) "$$o/$$n.c" > "$$o/$$n.log" 2>&1; \
	  else echo "$$n" >> "$$o/emitfail.txt"; fi; }; \
	grep -v '^#' tests/fixtures/run_cases.txt | awk 'NF >= 2 && !seen[$$1]++ {print $$1, $$2}' > $(PEDANTIC_DIR)/cases.txt; \
	fail=0; \
	for pair in $(PEDANTIC_TARGETS); do \
	  os=$${pair%%:*}; t=$${pair#*:}; \
	  case $$os in linux) cc="$(PEDANTIC_CC_linux)";; *) cc="$(PEDANTIC_CC_windows)";; esac; \
	  for part in drivers corpus; do mkdir -p $(PEDANTIC_DIR)/$$os/$$part; done; \
	  for d in zc zl zls; do one "$$cc" $(PEDANTIC_DIR)/$$os/drivers $$d $$t src & done; \
	  i=0; while read -r n dirs; do \
	    one "$$cc" $(PEDANTIC_DIR)/$$os/corpus "$$n" $$t "$$dirs" & \
	    i=$$((i + 1)); [ $$((i % $(NPROC))) -eq 0 ] && wait; \
	  done < $(PEDANTIC_DIR)/cases.txt; \
	  wait; \
	  for part in drivers corpus; do \
	    key=$$part-$$os; dir=$(PEDANTIC_DIR)/$$os/$$part; \
	    if [ -s $$dir/emitfail.txt ]; then \
	      echo "pedantic-check FAIL: zc did not emit these for $$t (the .zc file beside each says why):"; \
	      sed "s|^|  $$dir/|; s|$$|.zc|" $$dir/emitfail.txt; fail=1; continue; fi; \
	    want=3; [ $$part = corpus ] && want=$$(grep -c . $(PEDANTIC_DIR)/cases.txt); \
	    have=$$(ls $$dir | grep -c '\.log$$'); \
	    if [ "$$have" != "$$want" ]; then \
	      echo "pedantic-check FAIL: $$key: gcc ran on $$have of $$want files -- the check itself broke here"; fail=1; continue; fi; \
	    cat $$dir/*.log > $$dir.all; \
	    if grep -q ' error: ' $$dir.all; then \
	      echo "pedantic-check FAIL: gcc rejects the $$key C:"; grep -m5 ' error: ' $$dir.all; fail=1; continue; fi; \
	    got=$$(grep -c '\[-Wpedantic\]' $$dir.all); \
	    base=$$(awk -v k=$$key '$$1 == k {print $$2}' $(PEDANTIC_BASELINE)); \
	    if [ "$$got" != "$$base" ]; then \
	      echo "pedantic-check FAIL: $$key has $$got -Wpedantic diagnostics, $(PEDANTIC_BASELINE) says $$base"; \
	      echo "  a rise is C outside ISO C17 -- emit the portable form instead; a fall is progress --"; \
	      echo "  set '$$key $$got' in $(PEDANTIC_BASELINE) in the same commit. By kind:"; \
	      grep '\[-Wpedantic\]' $$dir.all | sed -E 's/^.*:[0-9]+:[0-9]+: warning: //' \
	        | sort | uniq -c | sort -rn | sed 's/^/  /'; \
	      fail=1; \
	    fi; \
	    printf '%s=%s ' $$key $$got >> $(PEDANTIC_DIR)/summary; \
	  done; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "pedantic-check OK: $$(cat $(PEDANTIC_DIR)/summary)-Wpedantic diagnostics, as the baseline says"

# perf -- the self-compile snapshot for docs/perf-baseline.md: line count, wall,
# peak RSS, phase split and the allocation total.
PERFARGS := zc --src src --system lib/system --emit-c /dev/null
PERFRUN  := $(PERFBIN) $(PERFARGS)

# The series binary: bin/zc.c compiled by PERFCC at PERFOPT.
$(PERFBIN): bin/zc.c $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o
	@$(PERFCC) -std=c17 -w $(PERFOPT) $(STAMPDEF) -o $@ $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o bin/zc.c $(call ZLINKOF,bin/zc.c) -lpthread -lm

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
ALLOC_BASELINE := 2788201
# ALLOC_LINE -- the one measurement every allocation number comes from.
ALLOC_LINE = valgrind --tool=memcheck $(PERFRUN) 2>&1 | grep 'total heap usage' | sed 's/.*usage: //'

ifdef WINDOWS_HOST
perf-strict:
	@echo "perf-strict: skipped (Linux only: it counts heap blocks under valgrind memcheck, which Windows does not have)"
else
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
endif

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
# suffix vocabulary, bound families, the `null` an as-block opt-out is spelled with,
# a template's own name picking the one collection family it could be, whose id
# then decides: templateIsCollection, templateNeedsMonoDtor).
TYPENAME_OK := src/ztypecheck.z:resolveClass src/ztypecheck.z:resolveProtocol \
	src/ztypecheck.z:defTypetypeOf src/ztypecheck.z:resolveObjectDef \
	src/ztypecheck.z:paramIsBorrowReftype src/ztypecheck.z:isBoxTemplate \
	src/ztypecheck.z:checkStaticListMember src/ztypecheck.z:checkMarkerMember \
	src/zemitterc.z:emitStaticListMember src/zemitterc.z:emitStrConvCall \
	src/zsource.z:bareZeroTypeName src/ztypecheck.z:constraintKindForId \
	src/ztypecheck.z:isConformanceMarker src/ztypecheck.z:declareCanonicalTypeId \
	src/ztypecheck.z:textLiteralCastTarget src/ztypecheck.z:isIterFamilyName \
	src/ztypecheck.z:isCollectionFamilyName src/ztypecheck.z:isHeapCollectionName
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
	chk "regNameOf" "$$e6" 4; \
	chk "mangleVarName (inside varCName)" "$$e7" 1; \
	chk "io.readText (the emitter reads no file: zc carries its texts)" "$$e8" 0; \
	chk "literal arm members (.data.<arm>; memberC spells them)" "$$e10" 0; \
	chk "poolFind (a re-intern of a name the emitter held as an id, or table text)" "$$e11" 11; \
	chk "member-name TEXT (C spelling and row keys only; look members up by aliasChildNameId)" "$$e12" 14; \
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
	  echo "  A backend fact belongs to the backend: the C emitter and its rows (lib/system/<unit>/nativec.zd)."; \
	  exit 1; \
	fi; \
	echo "frontend-guard OK: no C spelling, no triple parse and no target in the front end's code"

# lifetime-guard -- places the emitter still decides a lifetime itself rather than
# reading the checker's destroy lists. bindingRhsIsBorrow is a floor, not a
# target: it answers a C-shape question, not when anything dies. factsOfText
# counts the places that read an expression's facts off its C text rather than
# from its producer: none do, and the row keeps it so.
lifetime-guard:
	@l1=$$(grep -c 'registerScopeDestroy' src/zemitterc.z); \
	l2=$$(grep -c 'refsLocal' src/zemitterc.z); \
	l3=$$(grep -c 'factsOfText' src/zemitterc.z); \
	l4=$$(grep -c 'bindingRhsIsBorrow' src/zemitterc.z); \
	l5=$$(grep -cF '_ah\{' src/zemitterc.z); \
	fail=0; \
	chk() { if [ "$$2" -gt "$$3" ]; then echo "lifetime-guard FAIL: $$1 = $$2 (baseline $$3)"; fail=1; \
	  elif [ "$$2" -lt "$$3" ]; then echo "lifetime-guard: $$1 = $$2 < baseline $$3 -- lower the baseline here"; fi; }; \
	chk "registerScopeDestroy" "$$l1" 5; \
	chk "refsLocal" "$$l2" 4; \
	chk "factsOfText" "$$l3" 0; \
	chk "bindingRhsIsBorrow" "$$l4" 6; \
	chk "'_ah{' argument hoists" "$$l5" 0; \
	if [ "$$fail" = "1" ]; then \
	  echo "  The emitter decided a lifetime on its own again. Read the checker's destroy"; \
	  echo "  lists (scopeDestroy, exitDestroy) and the variable's recorded state instead."; \
	  exit 1; \
	fi; \
	echo "lifetime-guard OK: registerScopeDestroy=$$l1 refsLocal=$$l2 factsOfText=$$l3 bindingRhsIsBorrow=$$l4 argHoists=$$l5"

# sqlite-decls-guard -- the part of SQLite's C interface the sqlite unit's
# fragments declare instead of including (its @decls row) compiles after the
# vendored sqlite3.h without a diagnostic: a prototype, typedef or constant that
# drifts from the library fails here, rather than as a wrong call at run time.
sqlite-decls-guard: bin/zc
	@{ cat vendor/sqlite/sqlite3.h; bin/zc natives show sqlite.@decls --c --system lib/system; } \
	  | $(CC) -std=c17 -fsyntax-only -Wall -Werror -x c - \
	  || { echo "sqlite-decls-guard FAIL: sqlite's @decls row (lib/system/sqlite/nativec.zd) disagrees with vendor/sqlite/sqlite3.h -- match the declaration to the vendored header"; exit 1; }; \
	echo "sqlite-decls-guard OK: the sqlite fragments' declarations match the vendored sqlite3.h"

# cobj-guard -- a unit whose runtime is a C source of its own (a `source` in
# its nativec.zd) builds through the object cache: the first build compiles it
# (a miss), the second links the same object (a hit, the same inode), inproc
# and a spawned tcc get objects of their own, a changed define gets a new key,
# eight `zc cobj` at once build ONE object and leave no lock or partial file,
# a source missing from the vendor directory is refused by name, and a user
# unit that shares the unit's name but declares nothing native builds nothing.
cobj-guard: bin/zc $(BUILDDIR)/tcc
	@d=$$(mktemp -d); fail=0; F=tests/fixtures/cobj; \
	mkdir -p $$d/f43; cp -r $$F/. $$d/f43/; sed -i 's/COBJ_ANSWER=42/COBJ_ANSWER=43/' $$d/f43/cobjdemo/nativec.zd; \
	export ZEROLANG_VENDOR=$$PWD/$$F/vendor ZC_OBJ_CACHE=$$d/cache; \
	run() { bin/zc run cobjmain --system lib/system "$$@" 2>&1 | tr '\n' ' '; }; \
	got=$$(run --src $$F); [ "$$got" = "answer 42 " ] || { echo "cobj-guard FAIL: first build printed '$$got'"; fail=1; }; \
	n1=$$(ls $$d/cache/*.o 2>/dev/null | wc -l); i1=$$(ls -i $$d/cache/*.o 2>/dev/null); \
	[ "$$n1" = 1 ] || { echo "cobj-guard FAIL: the miss left $$n1 objects, want 1"; fail=1; }; \
	got=$$(run --src $$F); i2=$$(ls -i $$d/cache/*.o 2>/dev/null); \
	[ "$$got" = "answer 42 " ] && [ "$$i1" = "$$i2" ] || { echo "cobj-guard FAIL: the second build did not reuse the object ('$$got')"; fail=1; }; \
	got=$$(run --src $$F --cc $(BUILDDIR)/tcc --cc-mode inproc); [ "$$got" = "answer 42 " ] || { echo "cobj-guard FAIL: inproc printed '$$got'"; fail=1; }; \
	got=$$(run --src $$F --cc $(BUILDDIR)/tcc); [ "$$got" = "answer 42 " ] || { echo "cobj-guard FAIL: a spawned tcc printed '$$got'"; fail=1; }; \
	got=$$(run --src $$d/f43); [ "$$got" = "answer 43 " ] || { echo "cobj-guard FAIL: a changed define printed '$$got'"; fail=1; }; \
	n2=$$(ls $$d/cache/*.o | wc -l); [ "$$n2" = 4 ] || { echo "cobj-guard FAIL: $$n2 objects after four distinct keys, want 4"; fail=1; }; \
	bin/zc emit cobjmain --src $$F --system lib/system -o $$d/m.c; \
	grep -q '^/\* zcsrc: answer/answer.c COBJ_ANSWER=42 \*/' $$d/m.c || { echo "cobj-guard FAIL: the emitted header names no zcsrc"; fail=1; }; \
	export ZC_OBJ_CACHE=$$d/par; \
	for i in 1 2 3 4 5 6 7 8; do bin/zc cobj $$d/m.c > $$d/p$$i.txt 2>&1 & done; wait; \
	np=$$(cat $$d/p*.txt | sort -u | wc -l); no=$$(ls $$d/par/*.o | wc -l); left=$$(ls -a $$d/par | grep -cE 'lock|tmp'); \
	[ "$$np" = 1 ] && [ "$$no" = 1 ] && [ "$$left" = 0 ] || { echo "cobj-guard FAIL: 8 parallel builds gave $$np paths, $$no objects, $$left leftovers"; fail=1; }; \
	ZEROLANG_VENDOR=$$d/none bin/zc cobj $$d/m.c > $$d/miss.txt 2>&1; rc=$$?; \
	[ $$rc = 1 ] && grep -q "answer/answer.c" $$d/miss.txt || { echo "cobj-guard FAIL: a missing source was not refused by name (exit $$rc)"; fail=1; }; \
	bin/zc emit user_unit_sqlite --src tests/fixtures/user_units --system lib/system -o $$d/u.c > $$d/u.log 2>&1 \
	  && ! grep -q '^/\* zcsrc:' $$d/u.c || { echo "cobj-guard FAIL: a user unit named sqlite, declaring nothing native, asked for the SQLite source"; fail=1; }; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "cobj-guard OK: a unit's C source builds once per compiler, target and defines, shared by parallel builds"

# user-native-guard -- a unit outside the standard library with its own C
# implementation file (`<unit>/nativec.zd`) compiles, links and runs, one native in a hidden subunit, and a
# `when=` variant replaces its plain row exactly when its fact holds; and a
# generic native class (cellar.Cell) is instantiated per element type from the
# C template its class entry names, and a generic native function (cellar.holding)
# is the template function of the instance it answers; and an `os=windows` @unit row links
# for a windows target and nowhere else. Each unit's rows, fragments and
# templates are its own nativec.zd's.
# A user unit's class template is written whole where it has no member
# sections, and the member words a section may name are the runtime's own
# (memberWord): Cell's `puts` and `holding`, which one instance calls and
# another does not, are the user's C, as a hand-written shim's functions are.
USERTMPLCFLAGS := -Wno-unused-function

user-native-guard: bin/zc
	@d=$$(mktemp -d); fail=0; \
	bin/zc mystery --src tests/fixtures/user_native --system lib/system \
	  --emit-c $$d/mystery.c > $$d/emit.log 2>&1 \
	  || { echo "user-native-guard FAIL: emit"; sed -n 1,5p $$d/emit.log; fail=1; }; \
	if [ $$fail -eq 0 ]; then \
	  $(CC) $(CFLAGS) -o $$d/mystery $$d/mystery.c $(call ZLINKOF,$$d/mystery.c) -lm > $$d/cc.log 2>&1 \
	    || { echo "user-native-guard FAIL: the unit's fragment did not load (link)"; \
	         grep -m1 error $$d/cc.log; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  grep -q '^/\* zlink:.*pthread' $$d/mystery.c \
	    || { echo "user-native-guard FAIL: the held fact's @unit variant did not add its link= library"; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  got=$$($$d/mystery | tr '\n' ' '); \
	  if [ "$$got" != "49 42 3 63 " ]; then \
	    echo "user-native-guard FAIL: ran but printed '$$got', want '49 42 3 63 '"; fail=1; fi; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  bin/zc cellar --src tests/fixtures/user_native --system lib/system \
	    --emit-c $$d/cellar.c > $$d/cemit.log 2>&1 \
	    || { echo "user-native-guard FAIL: emit cellar"; sed -n 1,5p $$d/cemit.log; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  $(CC) $(CFLAGS) $(USERTMPLCFLAGS) -o $$d/cellar $$d/cellar.c $(call ZLINKOF,$$d/cellar.c) -lm > $$d/ccc.log 2>&1 \
	    || { echo "user-native-guard FAIL: the templated class did not compile"; grep -m3 error $$d/ccc.log; fail=1; }; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  got=$$($$d/cellar | tr '\n' ' '); \
	  if [ "$$got" != "42 false true 1 true true " ]; then \
	    echo "user-native-guard FAIL: cellar printed '$$got', want '42 false true 1 true true '"; fail=1; fi; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  for t in x86_64-w64-mingw32:win x86_64-linux-gnu:lin; do \
	    bin/zc cellar --src tests/fixtures/user_native --system lib/system \
	      --target $${t%%:*} --emit-c $$d/cellar_$${t#*:}.c > $$d/temit.log 2>&1 \
	      || { echo "user-native-guard FAIL: emit cellar for $${t%%:*}"; sed -n 1,5p $$d/temit.log; fail=1; }; \
	  done; \
	fi; \
	if [ $$fail -eq 0 ]; then \
	  $(ZLINKSED) $$d/cellar_win.c | grep -qw winmm && ! $(ZLINKSED) $$d/cellar_lin.c | grep -qw winmm \
	    || { echo "user-native-guard FAIL: an os=windows @unit row must link for a windows target and only there"; fail=1; }; \
	fi; \
	for u in mystery cellar; do \
	  bin/zc natives check rows tests/fixtures/user_native/$$u --system lib/system > $$d/rows.log 2>&1 \
	    || { echo "user-native-guard FAIL: zc natives check rows refuses $$u's rows"; sed -n 1,5p $$d/rows.log; fail=1; }; \
	done; \
	mkdir -p $$d/typo; cp -r tests/fixtures/user_native/mystery.z tests/fixtures/user_native/mystery $$d/typo/; \
	sed -i 's/key: "triple"/key: "tripel"/' $$d/typo/mystery/nativec.zd; \
	bin/zc natives check rows $$d/typo/mystery --system lib/system 2>&1 | grep -q 'row mystery.tripel names no declaration' \
	  || { echo "user-native-guard FAIL: zc natives check rows let a mistyped key (mystery.tripel) through"; fail=1; }; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "user-native-guard OK: a unit outside the standard library links and runs its own natives, a hidden subunit's among them, a fact's variant chosen, a generic and a non-generic class from their templates, a generic function from its result's, an os= row only for its OS; zc natives check rows passes both units and refuses a mistyped key"

# shim-guard -- the C thread shim (system's prelude.@zthread row, written to
# $(NATIVEC_INC)/zthread_shim.inc) passes its own test under
# every compiler zc drives and under ThreadSanitizer: tests/c/zthread_shim_test.c
# exercises each primitive under contention. TSan is proved first on a probe
# that races on purpose: a sanitizer that reports nothing there would make a
# clean shim run mean nothing. Where mingw-w64 and wine are installed the Win32
# backend is built and run too (in a wine prefix kept under out/, which takes
# seconds to create once); the OK line says which backends ran. On a Windows
# host gcc, clang and tcc build the Win32 backend natively, and TSan, which no
# Windows toolchain has, is skipped. MSVC is ci-windows's.
shim-guard: $(BUILDDIR)/tcc $(BUILDDIR)/wineprefix/.ready $(NATIVEC_INC)/zthread_shim.inc
	@d=$$(mktemp -d); fail=0; t=tests/c/zthread_shim_test.c; pr=tests/c/tsan_probe.c; si=-I$(NATIVEC_INC); \
	for cc in $(CC) clang; do \
	  $$cc -std=c17 -Wall -Wextra -Wno-unused-function -O2 $$si -o $$d/shim-$$cc $$t $(THREADLIB) > $$d/cc.log 2>&1 \
	    || { echo "shim-guard FAIL: $$cc did not build the shim test"; grep -m3 error $$d/cc.log; fail=1; continue; }; \
	  $$d/shim-$$cc > $$d/run.log 2>&1 || { echo "shim-guard FAIL: under $$cc"; cat $$d/run.log; fail=1; }; \
	done; \
	$(BUILDDIR)/tcc -B $(TCCLIB) -I $(TCCLIB)/include $$si -o $$d/shim-tcc $$t $(THREADLIB) > $$d/cc.log 2>&1 \
	  || { echo "shim-guard FAIL: tcc did not build the shim test"; grep -m3 error $$d/cc.log; fail=1; }; \
	[ -x $$d/shim-tcc ] && { $$d/shim-tcc > $$d/run.log 2>&1 || { echo "shim-guard FAIL: under tcc"; cat $$d/run.log; fail=1; }; }; \
	tsan=" and ThreadSanitizer (proved on a racy probe)"; \
	if [ -n "$(WINDOWS_HOST)" ]; then \
	  tsan=" -- the Win32 backend, natively; ThreadSanitizer skipped (Linux only: no Windows toolchain has it)"; \
	else \
	  $(CC) -g -fsanitize=thread -o $$d/probe $$pr -lpthread > $$d/cc.log 2>&1; \
	  $$d/probe > /dev/null 2> $$d/probe.err; \
	  grep -q "data race" $$d/probe.err || { echo "shim-guard FAIL: ThreadSanitizer did not report the probe's race; a clean TSan run proves nothing"; fail=1; }; \
	  $(CC) -std=c17 -g -O1 -fsanitize=thread $$si -o $$d/shim-tsan $$t -lpthread > $$d/cc.log 2>&1 \
	    || { echo "shim-guard FAIL: the TSan build"; fail=1; }; \
	  [ -x $$d/shim-tsan ] && { $$d/shim-tsan > $$d/run.log 2>&1 || { echo "shim-guard FAIL: under ThreadSanitizer"; head -30 $$d/run.log; fail=1; }; }; \
	fi; \
	win="; Win32 not run (needs $(MINGW_CC) 14 or newer, for -mcrtdll=ucrt, and wine: sudo apt install gcc-mingw-w64-x86-64 wine)"; \
	[ -n "$(WINDOWS_HOST)" ] && win=""; \
	if [ -z "$(WINDOWS_HOST)" ] && $(MINGW_UCRT_CHECK) && command -v wine >/dev/null 2>&1; then \
	  $(MINGW_CC) -std=c17 -Wall -Wextra -Wno-unused-function -O2 $(MINGW_CFLAGS) $$si -o $$d/shim.exe $$t > $$d/cc.log 2>&1 \
	    || { echo "shim-guard FAIL: mingw-w64 did not build the shim test"; grep -m3 error $$d/cc.log; fail=1; }; \
	  if [ -f $$d/shim.exe ]; then \
	    $(WINE_ENV) timeout 300 wine $$d/shim.exe > $$d/run.log 2>&1 \
	      || { echo "shim-guard FAIL: the Win32 backend under wine"; tail -20 $$d/run.log; fail=1; }; \
	    grep -q "zthread shim OK" $$d/run.log || { echo "shim-guard FAIL: the Win32 run did not report OK"; tail -20 $$d/run.log; fail=1; }; \
	  fi; \
	  win=", and the Win32 backend (mingw-w64 under wine)"; \
	fi; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "shim-guard OK: the thread shim's test passes under $(CC), clang, tcc$$tsan$$win"

# thread-tsan-guard -- every threaded run fixture (thread_*, sync_*) under
# ThreadSanitizer, built by $(CC) and by clang: no report, and the stdout and exit
# status its goldens hold. TSan is proved first on tests/c/tsan_probe.c, which
# races on purpose, so a sanitizer that reports nothing cannot pass the rest.
ifdef WINDOWS_HOST
thread-tsan-guard:
	@echo "thread-tsan-guard: skipped (Linux only: ThreadSanitizer, which no Windows toolchain has)"
else
thread-tsan-guard: bin/zc
	@d=$$(mktemp -d); fail=0; n=0; pr=tests/c/tsan_probe.c; \
	$(CC) -g -fsanitize=thread -o $$d/probe $$pr -lpthread > $$d/cc.log 2>&1; \
	$$d/probe > /dev/null 2> $$d/probe.err; \
	grep -q "data race" $$d/probe.err || { echo "thread-tsan-guard FAIL: ThreadSanitizer did not report the probe's race"; fail=1; }; \
	for f in tests/fixtures/emitc_corpus/thread_*.z tests/fixtures/emitc_corpus/sync_*.z; do \
	  [ -f $$f ] || continue; b=$$(basename $$f .z); \
	  bin/zc emit $$f --system lib/system -o $$d/$$b.c > $$d/emit.log 2>&1 \
	    || { echo "thread-tsan-guard FAIL: $$b did not emit"; fail=1; continue; }; \
	  want=0; [ -f tests/fixtures/run_golden/$$b.exit ] && want=$$(cat tests/fixtures/run_golden/$$b.exit); \
	  for cc in $(CC) clang; do \
	    $$cc -std=c17 -g -O1 -fsanitize=thread -w -o $$d/$$b-$$cc $$d/$$b.c $(call ZLINKOF,$$d/$$b.c,$$cc,-fsanitize=thread) -lm > $$d/cc.log 2>&1 \
	      || { echo "thread-tsan-guard FAIL: $$b did not build under $$cc"; grep -m3 error $$d/cc.log; fail=1; continue; }; \
	    $$d/$$b-$$cc > $$d/out.txt 2> $$d/err.txt; rc=$$?; n=$$((n+1)); \
	    if grep -q "ThreadSanitizer" $$d/err.txt; then echo "thread-tsan-guard FAIL: $$b under $$cc:"; head -20 $$d/err.txt; fail=1; fi; \
	    [ "$$rc" = "$$want" ] || { echo "thread-tsan-guard FAIL: $$b under $$cc exited $$rc, want $$want"; fail=1; }; \
	    cmp -s $$d/out.txt tests/fixtures/run_golden/$$b.out || { echo "thread-tsan-guard FAIL: $$b under $$cc printed other than its golden"; fail=1; }; \
	  done; \
	done; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "thread-tsan-guard OK: $$n threaded fixture runs race-free under ThreadSanitizer ($(CC), clang), proved on a racy probe"
endif

# case-guard -- every program declaring `main` is in a case list (run, smoke or
# dump), so something compiles it, and no list names a case twice. A unit with no
# main is reached through others.
case-guard:
	@fail=0; dup=0; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  grep -qE '^main: *function' $$f || continue; \
	  awk -v b="$$b" '$$1 == b {found = 1} END {exit !found}' \
	    tests/fixtures/run_cases.txt tests/fixtures/smoke_cases.txt tests/fixtures/dump_cases.txt \
	    || { echo "case-guard: $$b declares main but is in no case list -- nothing compiles it"; fail=1; }; \
	done; \
	for l in tests/fixtures/run_cases.txt tests/fixtures/smoke_cases.txt tests/fixtures/dump_cases.txt; do \
	  for d in $$(grep -v '^#' $$l | awk 'NF {print $$1}' | sort | uniq -d); do \
	    echo "case-guard: $$d is listed twice in $$l -- the runner would build and run it twice; delete one line"; dup=1; \
	  done; \
	done; \
	if [ $$fail -ne 0 ]; then \
	  echo "  add it to run_cases.txt with a tests/fixtures/run_golden/<name>.out, or to"; \
	  echo "  smoke_cases.txt when the output is environment-dependent (build only)"; \
	  exit 1; \
	fi; \
	if [ $$dup -ne 0 ]; then exit 1; fi; \
	echo "case-guard OK: every program declaring main is in a case list, and no list names one twice"

# require-guard -- how many programs are refused under --cc tcc, for a Linux and
# a Windows target whatever the host: a unit's toolchain needs speak only for
# programs that reach it. zlink-guard is the other half. Windows refuses one
# more, fold_guarded_f128, whose f128 arm only a Windows build reaches.
REQUIRE_TCC_BASELINE := 8
REQUIRE_TCC_BASELINE_WINDOWS := 9

require-guard: bin/zc
	@fail=0; \
	for tb in x86_64-linux-gnu:$(REQUIRE_TCC_BASELINE) x86_64-w64-mingw32:$(REQUIRE_TCC_BASELINE_WINDOWS); do \
	  t=$${tb%%:*}; base=$${tb#*:}; n=0; rep=""; \
	  for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	    b=$$(basename $$f .z); \
	    if bin/zc emit $$f --system lib/system --cc tcc --target $$t -o /dev/null 2>&1 | grep -qE 'error\[E0601\]|from the C compiler, and tcc has none'; then \
	      n=$$(($$n + 1)); rep="$$rep  $$b\n"; \
	    fi; \
	  done; \
	  if [ "$$n" -ne "$$base" ]; then \
	    echo "require-guard FAIL: $$n program(s) rejected under --cc tcc for $$t (baseline $$base)"; \
	    printf "$$rep"; \
	    echo "  a unit's needs speak only for a program that REACHES it."; \
	    fail=1; \
	  fi; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "require-guard OK: programs rejected under --cc tcc: $(REQUIRE_TCC_BASELINE) for Linux, $(REQUIRE_TCC_BASELINE_WINDOWS) for Windows (the baselines)"

# mode-parity -- --cc-mode inproc builds the same PROGRAM as spawn: every run case
# is built both ways and its output and exit code compared (never the bytes).
# Refusals must match too. Only zc's temp-path pid is normalised. Each program
# reads an empty stdin, as the test runner gives it: the list of cases is this
# loop's stdin, and a program reading its own would eat the cases after it.
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
	        < /dev/null > $(BUILDDIR)/parity/$$name.$$mode.out 2>&1; \
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
	  --target $(HOST_TARGET) -o $$d/g > $$d/g.log 2>&1; rc=$$?; \
	if [ $$rc -ne 0 ] || [ "$$($$d/g | head -1)" != "os=$(HOST_OSNAME)" ]; then \
	  echo "refusal-guard FAIL: a HOST --target must still build and fold to the host (exit $$rc)"; \
	  cat $$d/g.log; bad=1; \
	fi; \
	for fl in --src --system --emit-c --dump-sql -o --cc --cc-mode --tcc-lib --target --cflags --ldflags; do \
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
	for arg in --output-dir --bogus-flag; do \
	  bin/zc build hello --src examples --system lib/system $$arg x -o $$d/unk > $$d/unk.log 2>&1; rc=$$?; \
	  if [ $$rc -ne 2 ] || [ -e $$d/unk ] || ! grep -q "unknown option '$$arg'" $$d/unk.log; then \
	    echo "refusal-guard FAIL: an unknown option ($$arg) must be refused with exit 2, got $$rc"; \
	    cat $$d/unk.log; bad=1; \
	  fi; \
	done; \
	bin/zc emit hello --src examples --system lib/system extra -o $$d/opnd.c > $$d/opnd.log 2>&1; rc=$$?; \
	if [ $$rc -ne 2 ] || [ -e $$d/opnd.c ] || ! grep -q "unexpected argument 'extra'" $$d/opnd.log; then \
	  echo "refusal-guard FAIL: a second operand to zc emit must be refused with exit 2, got $$rc"; \
	  cat $$d/opnd.log; bad=1; \
	fi; \
	if [ $$bad -ne 0 ]; then \
	  echo "  zc never accepts a flag it will ignore: it names what it resolved and exits 2."; \
	  exit 1; \
	fi; \
	echo "refusal-guard OK: every unhonourable flag combination is refused, and its honoured counterpart still works"

# static-tcc-guard -- the vendored tinycc is LGPL-2.1, so the drivers may only
# dlopen it: no libtcc symbol may appear in them (see vendor/tinycc/VERSION.md).
# STATIC_TCC_BINS overrides the binaries checked, to falsify the guard. A binary
# whose symbols cannot be read fails it: "found no libtcc symbol" proves nothing
# about a file nm never opened (on Windows the drivers are bin/zc.exe ...).
STATIC_TCC_BINS ?= bin/zc$(EXE) bin/zl$(EXE) bin/zls$(EXE)
static-tcc-guard: bin/zc bin/zl bin/zls
	@fail=0; \
	for b in $(STATIC_TCC_BINS); do \
	  if ! nm -A $$b > /dev/null 2>&1; then \
	    echo "static-tcc-guard FAIL: cannot read the symbols of $$b"; fail=1; continue; fi; \
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

# zlink-guard -- how many programs declare a link library, for a Linux and a
# Windows target whatever the host: a unit's library goes to the programs that
# reach it and no others. The sets differ by system -- pthread for sync and
# thread on Linux; advapi32 and shell32 for os, ws2_32 for net on Windows.
ZLINK_BASELINE := 32
ZLINK_BASELINE_WINDOWS := 39

zlink-guard: bin/zc
	@fail=0; \
	for tb in x86_64-linux-gnu:$(ZLINK_BASELINE) x86_64-w64-mingw32:$(ZLINK_BASELINE_WINDOWS); do \
	  t=$${tb%%:*}; base=$${tb#*:}; n=0; rep=""; \
	  for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	    b=$$(basename $$f .z); \
	    l=$$(bin/zc emit $$f --system lib/system --target $$t 2>/dev/null \
	          | $(ZLINKSED)); \
	    if [ -n "$$l" ]; then n=$$(($$n + 1)); rep="$$rep  $$b: $$l\n"; fi; \
	  done; \
	  if [ "$$n" -ne "$$base" ]; then \
	    echo "zlink-guard FAIL: $$n program(s) declare a link library for $$t (baseline $$base)"; \
	    printf "$$rep"; \
	    fail=1; \
	  fi; \
	done; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "zlink-guard OK: programs declaring a link library: $(ZLINK_BASELINE) for Linux, $(ZLINK_BASELINE_WINDOWS) for Windows (the baselines)"

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

# const-row-guard -- a nativec row with a `constant` position is chosen exactly when its operand
# is a compile-time constant: const_divide_form divides by a variable four times,
# and each of those must call the helper that checks the divisor; its constant
# divisors divide inline.
CONST_ROW_VARIABLE := 4

const-row-guard: bin/zc
	@d=$$(mktemp -d); em=$$d/emitted.c; \
	bin/zc emit tests/fixtures/emitc_corpus/const_divide_form.z -o $$em || { \
	  echo "const-row-guard FAIL: const_divide_form did not emit"; rm -rf $$d; exit 1; }; \
	n=$$(grep -v '^static' $$em | grep -oE 'z_(div|rem)_[a-z0-9]+\(' | wc -l); rm -rf $$d; \
	if [ "$$n" -ne $(CONST_ROW_VARIABLE) ]; then \
	  echo "const-row-guard FAIL: $$n divisor-checking calls, expected $(CONST_ROW_VARIABLE)"; \
	  echo "  More means a constant divisor stopped selecting the const= row."; \
	  echo "  Fewer means a variable divisor selected it, and that division is"; \
	  echo "  no longer checked for zero."; \
	  exit 1; \
	fi; \
	echo "const-row-guard OK: $$n variable divisors checked, the constant ones divide inline"

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

# cdead-guard -- every top-level C item the emitter writes is reached from
# `main`. No C compiler reports an unused type, macro or static inline function,
# or a function only an unused one calls, so the C is read here: the C of each
# corpus program and of zc itself, once for each preprocessor configuration the
# C branches on (C17 and C23, the portable integer and float code with and
# without __float128, clang, tcc, MSVC, MinGW). Each top-level item -- function,
# prototype, type, enumerator, static, macro -- is owned by the names it defines
# and reaches every name it mentions. `main`, a name an #if tests and a #define
# that configures a system header are the roots. An item no configuration reaches is dead, and an item
# the reader cannot name fails the guard. tests/c/cdead_probe.c holds dead items
# of every kind, and live ones only a careless reader misses: the reader must
# report exactly CDEAD_PROBE there before its count is trusted. The baseline is
# the dead items the emitter writes today (corpus, then zc); lower it as it falls.
CDEAD_BASELINE := 0 0
CDEAD_PROBE := cdead_dead_ext_t cdead_dead_fn cdead_dead_fn_ft cdead_dead_inline cdead_dead_leaf \
	cdead_dead_macro cdead_dead_proto_t cdead_dead_str cdead_dead_t cdead_dead_vtable_t \
	cdead_dead_word_fn

define CDEAD_GUARD_PY
import re, subprocess, sys
from collections import defaultdict
# the preprocessor configurations the emitted C branches on; reach is their union
CONFIGS = [
    ['-std=c17'],
    ['-std=c23'],
    ['-std=c17', '-DZ_WORD_PORTABLE', '-DZ_INT128_PORTABLE', '-DZ_F16_PORTABLE',
     '-U__SIZEOF_FLOAT128__', '-U__SIZEOF_INT128__', '-U__FLT16_MANT_DIG__'],
    # make test-portable-scalars: the struct forms beside the compiler's __float128
    ['-std=c17', '-DZ_INT128_PORTABLE', '-DZ_F16_PORTABLE'],
    ['-std=c17', '-D__clang__'],
    ['-undef', '-D__TINYC__', '-D__linux__', '-D__x86_64__', '-D__STDC_VERSION__=201112L'],
    ['-undef', '-D_WIN32', '-D_WIN64', '-D_MSC_VER=1940', '-D_M_X64', '-D__STDC_VERSION__=201710L'],
    ['-std=c17', '-U__linux__', '-U__linux', '-Ulinux', '-U__unix__', '-U__unix', '-Uunix',
     '-D_WIN32', '-D_WIN64'],
]
SENT = 'int cdead_sentinel_;'
# a name, but not a member after `.` or `->`
ID = re.compile(r'(?<![.\w>])(?<!->)[A-Za-z_]\w*')
ATTR = re.compile(r'\b(__attribute__|__declspec)\s*\(')

def pre(text, flags):
    """the file's own text for one configuration: conditionals resolved, macros
    kept, system headers not read"""
    src = SENT + '\n' + re.sub(r'(?m)^\s*#\s*include\b.*$$', '', text)
    out = subprocess.run(['gcc', '-E', '-P', '-fdirectives-only', '-nostdinc', *flags, '-x', 'c', '-'],
                         input=src, capture_output=True, text=True).stdout
    return out.split(SENT, 1)[1]

def strip(t):
    # one pass, left to right: a `//` or `/*` inside a literal is the
    # literal's, and a quote inside a comment is the comment's
    t = re.sub(r'\\\n', ' ', t)
    def one(m):
        s = m.group(0)
        if s.startswith('"'): return '""'
        if s.startswith("'"): return '0'
        return ' '
    return re.sub(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|/\*.*?\*/|//[^\n]*', one, t, flags=re.S)

def unattr(t):
    while True:
        m = ATTR.search(t)
        if not m: return t
        i, d = m.end(), 1
        while d and i < len(t):
            d += {'(': 1, ')': -1}.get(t[i], 0); i += 1
        t = t[:m.start()] + ' ' + t[i:]

def chunks(text):
    """the top-level items, one string each: a directive, or the lines up to a
    `;` or `}` at brace depth 0"""
    out, cur, depth = [], [], 0
    for line in text.split('\n'):
        s = line.strip()
        if not s: continue
        if depth == 0 and not cur and s.startswith('#'):
            out.append(s); continue
        cur.append(s); depth += s.count('{') - s.count('}')
        if depth == 0 and s.endswith((';', '}')):
            out.append(' '.join(cur)); cur = []
    if cur: out.append(' '.join(cur))
    return out

def top_paren(head):
    d = 0
    for i, c in enumerate(head):
        if c == '(' and d == 0: return i
        d += {'(': 1, ')': -1, '[': 1, ']': -1}.get(c, 0)
    return -1

def enumerators(body):
    inner = body[1:body.find('}')]
    return [m.group(1) for m in re.finditer(r'(?:^|,)\s*(\w+)\s*(?:=[^,]*)?(?=,|$$)', inner)]

def classify(ch):
    """(kind, names) for one top-level item; no names when it defines none"""
    if ch.startswith('#'):
        m = re.match(r'#\s*define\s+(\w+)', ch)
        return ('macro', [m.group(1)]) if m else (None, [])
    # `__extension__` only quiets -pedantic: the item is what follows it
    t = re.sub(r'^(__extension__\s+)+', '', unattr(ch))
    if re.match(r'(_Static_assert|static_assert)\b', t): return (None, [])
    b = t.find('{')
    head = t if b < 0 else t[:b]
    if re.match(r'typedef\b', t):
        m = re.search(r'\(\s*(?:\w+\s+)?\*\s*(\w+)\s*\)\s*\(', t) if b < 0 else None
        names = [m.group(1)] if m else []
        if not m:
            tail = re.sub(r'\[[^\]]*\]', '', t[t.rfind('}') + 1:] if b >= 0 else t)
            ids = ID.findall(tail.rstrip(' ;'))
            if ids: names.append(ids[-1])
        tm = re.match(r'typedef\s+(?:const\s+)?(?:struct|union|enum)\s+(\w+)', t)
        if tm and b >= 0 and tm.group(1) not in names: names.append(tm.group(1))
        if re.match(r'typedef\s+enum\b', t) and b >= 0: names += enumerators(t[b:])
        return ('type', names)
    # an anonymous enum is one item, owned by every constant it declares
    if re.match(r'enum\s*\{', t) and b >= 0:
        return ('type', enumerators(t[b:]))
    m = re.match(r'(?:(?:static|const|volatile|extern|inline)\s+)*(struct|union|enum)\s+(\w+)\s*([{;])', t)
    if m and (m.group(3) == ';' or t.endswith('};') and not re.search(r'\}\s*\w+\s*(\[[^\]]*\])?\s*(=|;)', t)):
        names = [m.group(2)]
        if m.group(1) == 'enum' and b >= 0: names += enumerators(t[b:])
        return ('type', names)
    p = top_paren(head)
    eq = head.find('=')
    if p > 0 and (eq < 0 or eq > p):
        ids = ID.findall(head[:p])
        if ids and not re.match(r'(if|while|for|switch|return|sizeof)$$', ids[-1]):
            return ('fn', [ids[-1]])
    cut = len(t)
    for c in ('=', ';'):
        i = t.find(c)
        if 0 <= i < cut: cut = i
    ids = ID.findall(re.sub(r'\[[^\]]*\]', '', t[:cut]))
    return ('var', [ids[-1]]) if ids else (None, [])

def raw_roots(raw):
    """the names the preprocessor itself consumes: those an #if tests, and each
    #define directly ahead of an #include (it configures a system header)"""
    roots, pending = set(), set()
    for line in strip(raw).split('\n'):
        s = line.strip()
        if not s: continue
        m = re.match(r'#\s*(if|ifdef|ifndef|elif)\b(.*)', s)
        if m: roots |= set(ID.findall(m.group(2))); continue
        m = re.match(r'#\s*define\s+(\w+)', s)
        if m: pending.add(m.group(1)); continue
        if re.match(r'#\s*include\b', s): roots |= pending
        if not re.match(r'#\s*(else|endif)\b', s): pending = set()
    return roots

def analyse(path):
    """the dead items of one file, {first name: kind}, and the items it could
    not name"""
    raw = open(path, errors='replace').read()
    reached, groups, unowned = set(), {}, []
    roots = raw_roots(raw) | {'main'}
    for flags in CONFIGS:
        own = defaultdict(set)
        for ch in chunks(strip(pre(raw, flags))):
            kind, names = classify(ch)
            if not names:
                if not re.match(r'#\s*(undef|pragma)|(_Static_assert|static_assert)\b', ch):
                    unowned.append(ch[:80])
                continue
            refs = set(ID.findall(ch if ch.startswith('#') else unattr(ch))) - set(names)
            for n in names: own[n] |= refs
            groups.setdefault(names[0], [kind, set()])[1].update(names)
        seen, todo = set(), set(roots)
        while todo:
            n = todo.pop()
            if n in seen: continue
            seen.add(n)
            todo |= own.get(n, set()) - seen
        reached |= seen
    # an item (a prototype and its definition are one) is dead when no
    # configuration reaches any name it defines
    return {p: k for p, (k, ns) in groups.items() if not (ns & reached)}, sorted(set(unowned))

listing = sys.argv[1] == '--list'
tot = defaultdict(int); uo = 0
for p in sys.argv[2 if listing else 1:]:
    items, unowned = analyse(p)
    uo += len(unowned)
    for n, k in sorted(items.items()):
        tot[k] += 1
        if listing: print('DEAD %s %s' % (k, n))
    for u in unowned: print('UNOWNED %s: %s' % (p.rsplit('/', 1)[-1], u))
print('KINDS', ' '.join('%s=%d' % kv for kv in sorted(tot.items())))
print('TOTAL', sum(tot.values()), uo)
endef
export CDEAD_GUARD_PY

# GUARD_EMIT_C -- write the C of every corpus program into $$d, and zc's own C
# into $$d/zc/zc.c, for the guards that read emitted C. A program that does not
# emit is left out: the corpus runner reports it.
GUARD_EMIT_C = mkdir -p $$d/zc; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); bin/zc emit $$f -o $$d/$$b.c 2>/dev/null || rm -f $$d/$$b.c; \
	done; \
	bin/zc zc --src src --system lib/system $(ZCHASH) --emit-c $$d/zc/zc.c >/dev/null

cdead-guard: bin/zc
	@d=$$(mktemp -d); \
	got=$$(python3 -c "$$CDEAD_GUARD_PY" --list tests/c/cdead_probe.c | sed -n 's/^DEAD [a-z]* //p' | tr '\n' ' '); \
	if [ "$$got" != "$$(echo $(CDEAD_PROBE)) " ]; then \
	  echo "cdead-guard FAIL: the reader does not see tests/c/cdead_probe.c's dead items"; \
	  echo "  want: $(CDEAD_PROBE)"; echo "  got:  $$got"; rm -rf $$d; exit 1; \
	fi; \
	$(GUARD_EMIT_C); \
	ls $$d/*.c | xargs -P $(NPROC) -n 40 python3 -c "$$CDEAD_GUARD_PY" > $$d/report; \
	python3 -c "$$CDEAD_GUARD_PY" $$d/zc/zc.c > $$d/zcreport; \
	n=$$(awk '/^TOTAL/ {s += $$2} END {print s}' $$d/report); z=$$(awk '/^TOTAL/ {print $$2}' $$d/zcreport); \
	u=$$(cat $$d/report $$d/zcreport | awk '/^TOTAL/ {s += $$3} END {print s}'); \
	kinds=$$(cat $$d/report $$d/zcreport | tr ' ' '\n' | grep = | awk -F= '{a[$$1] += $$2} END {for (k in a) printf " %s=%d", k, a[k]}'); \
	set -- $(CDEAD_BASELINE); \
	if [ "$$u" -gt 0 ]; then \
	  echo "cdead-guard FAIL: $$u top-level C items the reader cannot name"; grep '^UNOWNED' $$d/report $$d/zcreport | head -20; \
	  rm -rf $$d; exit 1; \
	elif [ "$$n" -gt "$$1" ] || [ "$$z" -gt "$$2" ]; then \
	  echo "cdead-guard FAIL: $$n dead C items in the corpus, $$z in zc (baseline $(CDEAD_BASELINE));$$kinds"; \
	  echo "  A C item written that main does not reach: see emitC in src/zemitterc.z."; \
	  rm -rf $$d; exit 1; \
	elif [ "$$n" -lt "$$1" ] || [ "$$z" -lt "$$2" ]; then \
	  echo "cdead-guard: $$n $$z < baseline $(CDEAD_BASELINE) -- lower CDEAD_BASELINE here"; \
	else \
	  echo "cdead-guard OK: $$n dead C items in the corpus, $$z in zc (baseline $(CDEAD_BASELINE));$$kinds"; \
	fi; \
	rm -rf $$d

# cc-unused-guard -- gcc and clang find no unused file-scope item in the
# emitted C: the C of each corpus program and of zc itself is compiled in each
# configuration the C branches on that this host can run (default, the portable
# integers and float, the portable word) with -Wunused-function
# -Wunused-variable -Wunused-const-variable, and the warnings on FILE-SCOPE
# items counted -- static functions and variables; a local, an indented line,
# is not (CC_UNUSED_PY). An undeclared function is an error
# (-Werror=implicit-function-declaration), so C conditioned on a narrower test
# than its callers fails as surely as C conditioned on a wider one stays
# unused. Both compilers must first report exactly CC_UNUSED_PROBE_<cc> in
# tests/c/cdead_probe.c across the configurations (its unused local not among
# them) and an error for a call to an undeclared function, so a compiler, a
# configuration or a filter that stops seeing them cannot pass. The baseline is
# today's count summed over the configurations (gcc corpus, gcc zc, clang
# corpus, clang zc, mingw-w64 corpus, mingw-w64 zc -- the Windows branches);
# lower it as it falls. Skipped without clang; mingw-w64's pair is not measured
# without it ("-").
CC_UNUSED_BASELINE := 0 0 0 0 0 0
CC_UNUSED_FLAGS := -std=c17 -c -o /dev/null -Wunused-function -Wunused-variable -Wunused-const-variable \
	-Werror=implicit-function-declaration
CC_UNUSED_PROBE_gcc := cdead_dead_fn cdead_dead_str cdead_dead_word_fn
# clang also reports an unused static inline function (gcc never does)
CC_UNUSED_PROBE_clang := cdead_dead_fn cdead_dead_inline cdead_dead_str cdead_dead_word_fn
CC_UNUSED_PROBE_mingw := $(CC_UNUSED_PROBE_gcc)
# the compilers: mingw-w64 compiles the same C for Windows
CC_UNUSED_CCS = gcc clang $(if $(shell command -v $(MINGW_CC) 2>/dev/null),mingw)
CC_UNUSED_CMD = $(if $(filter mingw,$(1)),$(MINGW_CC) $(MINGW_CFLAGS),$(1))

define CC_UNUSED_PY
import re, sys
# the file-scope unused warnings, and the errors, a compiler wrote on stdin for
# the file argv[1]; --list names each
path = sys.argv[1]; listing = '--list' in sys.argv
src = open(path, errors='replace').read().split('\n')
n = 0; errs = 0
for ln in sys.stdin:
    if ': error:' in ln:
        errs += 1
        if listing: print('ERROR ' + ln.strip())
        continue
    m = re.match(r'(.*?):(\d+):\d+: warning: (.*)\[-Wunused-(function|variable|const-variable)=?\]', ln)
    if not m or m.group(1) != path: continue
    no = int(m.group(2))
    text = src[no - 1] if no <= len(src) else ''
    if m.group(4) == 'variable' and text[:1] in (' ', '\t'): continue
    n += 1
    if listing:
        nm = re.search(r"['‘]([A-Za-z_]\w*)['’]", m.group(3))
        print('UNUSED ' + (nm.group(1) if nm else '?'))
print('COUNT %d %d' % (n, errs))
endef
export CC_UNUSED_PY

# CC_UNUSED_CFGS -- the configurations, one per line of flags (the first empty)
CC_UNUSED_CFGS = "" "-DZ_INT128_PORTABLE -DZ_F16_PORTABLE" "-DZ_WORD_PORTABLE"

cc-unused-guard: bin/zc
	@command -v clang >/dev/null 2>&1 || { echo "cc-unused-guard SKIP: clang not installed"; exit 0; }; \
	d=$$(mktemp -d); \
	printf 'int main(void) { return cdead_undeclared_fn(); }\n' > $$d/undeclared.c; \
	for cc in $(CC_UNUSED_CCS); do \
	  ccx="$$cc"; [ $$cc = mingw ] && ccx="$(MINGW_CC) $(MINGW_CFLAGS)"; \
	  got=$$(for cfg in $(CC_UNUSED_CFGS); do \
	    $$ccx $(CC_UNUSED_FLAGS) $$cfg tests/c/cdead_probe.c 2>&1 \
	      | python3 -c "$$CC_UNUSED_PY" tests/c/cdead_probe.c --list; done \
	    | sed -n 's/^UNUSED //p' | sort -u | tr '\n' ' '); \
	  want="$(CC_UNUSED_PROBE_gcc) "; [ $$cc = clang ] && want="$(CC_UNUSED_PROBE_clang) "; \
	  [ $$cc = mingw ] && want="$(CC_UNUSED_PROBE_mingw) "; \
	  if [ "$$got" != "$$want" ]; then \
	    echo "cc-unused-guard FAIL: $$cc does not report exactly tests/c/cdead_probe.c's file-scope dead items"; \
	    echo "  want: $$want"; echo "  got:  $$got"; rm -rf $$d; exit 1; \
	  fi; \
	  e=$$($$ccx $(CC_UNUSED_FLAGS) $$d/undeclared.c 2>&1 | python3 -c "$$CC_UNUSED_PY" $$d/undeclared.c | awk '{print $$3}'); \
	  if [ "$$e" = "0" ]; then \
	    echo "cc-unused-guard FAIL: $$cc does not reject a call to an undeclared function"; rm -rf $$d; exit 1; \
	  fi; \
	done; \
	$(GUARD_EMIT_C); \
	got=""; errs=0; \
	for cc in gcc clang mingw; do \
	  case " $(CC_UNUSED_CCS) " in *" $$cc "*) ;; *) got="$$got - -"; continue;; esac; \
	  ccx="$$cc"; [ $$cc = mingw ] && ccx="$(MINGW_CC) $(MINGW_CFLAGS)"; \
	  c=0; z=0; \
	  for cfg in $(CC_UNUSED_CFGS); do \
	    ls $$d/*.c | grep -v '/undeclared.c$$' | xargs -P $(NPROC) -I{} sh -c "$$ccx $(CC_UNUSED_FLAGS) $$cfg {} 2>&1 | python3 -c \"\$$CC_UNUSED_PY\" {}" > $$d/count; \
	    c=$$((c + $$(awk '{s += $$2} END {print s + 0}' $$d/count))); errs=$$((errs + $$(awk '{s += $$3} END {print s + 0}' $$d/count))); \
	    $$ccx $(CC_UNUSED_FLAGS) $$cfg $$d/zc/zc.c 2>&1 | python3 -c "$$CC_UNUSED_PY" $$d/zc/zc.c > $$d/count; \
	    z=$$((z + $$(awk '{print $$2}' $$d/count))); errs=$$((errs + $$(awk '{print $$3}' $$d/count))); \
	  done; \
	  got="$$got $$c $$z"; \
	done; \
	rm -rf $$d; \
	if [ $$errs -ne 0 ]; then \
	  echo "cc-unused-guard FAIL: $$errs compile error(s) in a configuration: C calls a function its test leaves out"; exit 1; \
	fi; \
	over=$$(echo "$$got / $(CC_UNUSED_BASELINE)" | awk '{n = (NF - 1) / 2; for (i = 1; i <= n; i++) if ($$i != "-" && $$i > $$(i + n + 1)) o = 1; print o + 0}'); \
	same=$$(echo "$$got / $(CC_UNUSED_BASELINE)" | awk '{n = (NF - 1) / 2; for (i = 1; i <= n; i++) if ($$i != "-" && $$i != $$(i + n + 1)) d = 1; print d + 0}'); \
	if [ "$$over" = 1 ]; then \
	  echo "cc-unused-guard FAIL: unused file-scope functions/variables:$$got (baseline $(CC_UNUSED_BASELINE))"; \
	  echo "  A C item written that nothing calls in some configuration: see emitC in src/zemitterc.z."; exit 1; \
	elif [ "$$same" = 1 ]; then \
	  echo "cc-unused-guard:$$got < baseline $(CC_UNUSED_BASELINE) -- lower CC_UNUSED_BASELINE here"; \
	else \
	  echo "cc-unused-guard OK: unused file-scope functions/variables:$$got (baseline $(CC_UNUSED_BASELINE))"; \
	fi

# piece-guard -- a piece of a fixed file or of a row's fragment is written only
# where written C needs it (loadPieces in src/zemitterc.z), so every text that
# calls what a piece defines must say so: a row and another piece by its needs,
# a template by its needs and its sections', and the emitter by `preludeNeed
# :ctx memb: "@<piece>"` in the definition that spells the name. Text outside
# every piece may call none. Each piece closes the #if it opens, each piece name
# is defined once, a need's condition is declared, a row with pieces is the
# prelude's or answers @unit, one demand word fires one class's members, and a
# fragment calling an instance's member by its canon names it in members
# (`zc natives check needs`, src/znativepieces.z). It first runs on a copy of the
# emitter with one record removed, and must report it.
PIECE_GUARD_DIR := $(BUILDDIR)/piece-guard
piece-guard: bin/zc
	@mkdir -p $(PIECE_GUARD_DIR); \
	grep -vF 'preludeNeed :ctx memb: "@quadmath_snprintf"' src/zemitterc.z > $(PIECE_GUARD_DIR)/zemitterc.z; \
	got=$$(bin/zc natives check needs --emitter $(PIECE_GUARD_DIR)/zemitterc.z --system lib/system | grep -c 'FAIL.*quadmath_snprintf'); \
	if [ "$$got" = 0 ]; then \
	  echo "piece-guard FAIL: an emitter record removed gives no report"; exit 1; \
	fi; \
	bin/zc natives check needs --emitter src/zemitterc.z --system lib/system

# static-value-guard -- every static value the emitter writes (`_zag<row>`) is
# read by the C: a value no run-time code reaches is not written (clang's
# -Wunused-const-variable; gcc warns only past -fsyntax-only), and so is every
# string array (`_zs<n>`, `_zcs<n>`): a string folded and read only at compile
# time writes none (text_fold_run). And a list read
# only at compile time writes no view type: static_list_folded_only's C holds no
# `{ length; const T* data; }` struct. Skipped without clang.
static-value-guard: bin/zc
	@v=$$(bin/zc emit tests/fixtures/emitc_corpus/static_list_folded_only.z 2>/dev/null \
	  | grep -A1 '^    uint64_t length;$$' | grep -c '^    const .*\* data;$$'); \
	if [ "$$v" -ne 0 ]; then \
	  echo "static-value-guard FAIL: a list read only at compile time wrote $$v view types"; \
	  echo "  See staticListReached in src/zemitterc.z."; exit 1; \
	fi
	@command -v clang >/dev/null 2>&1 || { echo "static-value-guard SKIP: clang not installed"; exit 0; }; \
	d=$$(mktemp -d); n=0; rep=""; \
	for f in examples/*.z tests/fixtures/emitc_corpus/*.z; do \
	  b=$$(basename $$f .z); \
	  bin/zc emit $$f -o $$d/$$b.c 2>/dev/null || continue; \
	  c=$$(clang -fsyntax-only -std=c17 -Wunused-const-variable $$d/$$b.c 2>&1 | grep -cE "unused variable '_z(ag|c?s)[0-9]"); \
	  n=$$(($$n + $$c)); \
	  if [ "$$c" -gt 0 ]; then rep="$$rep  $$b: $$c\n"; fi; \
	done; \
	rm -rf $$d; \
	if [ "$$n" -gt 0 ]; then \
	  echo "static-value-guard FAIL: $$n static values or strings written that nothing reads"; \
	  printf "$$rep"; \
	  echo "  A value marked reached where no run-time read remains. See markAggReached in src/ztypecheck.z."; \
	  exit 1; \
	fi; \
	echo "static-value-guard OK: every static value written is read"

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
EAGER_KNOWN := unused_definition_not_demanded unused_method_not_demanded generic_member_not_demanded generic_unit_member_not_demanded user_never_not_demanded user_unit_public_instance_lazy user_unit_public_reexport_lazy user_unit_namespace_lazy user_unit_entry_demand

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
# Python on Windows ends each printed line with \r\n; tr keeps the sets comparable.
jsset() {
  python3 -c "
import re,sys
s=open('docs/style/prism-zerolang.js', encoding='utf-8').read()
m=re.search(r'var '+sys.argv[1]+r' = \[(.*?)\];',s,re.S)
print('\n'.join(sorted(set(re.findall(r\"'([^']*)'\",m.group(1))))))" "$$1" | tr -d '\r'
}
rbset() {
  python3 -c "
import re,sys
s=open('editor/rouge/zerolang.rb', encoding='utf-8').read()
m=re.search(r'def self\.'+sys.argv[1]+r'\b.*?%w\((.*?)\)',s,re.S)
print('\n'.join(sorted(set(m.group(1).split()))))" "$$1" | tr -d '\r'
}

grep -oE '^[A-Za-z_][A-Za-z0-9_]*:' lib/system/core.z | sed 's/:$$//' | sort -u > "$$D/core"
# the keywords are what each kw*Id function returns, spelled beside its label in
# zast's wellKnown block (read from the construction, so a new family needs no edit).
wkspell() {
  sed -n '/^wellKnown: list {/,/^}/p' lib/system/zast.z | awk -v want="$$1" '
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

# view-guard -- a native receiver marked `.view` is const in the C function that
# takes it, and a const one is marked `.view`, so the C compiler proves a viewed
# receiver is never written (`zc natives check views`, src/znativeview.z). The
# register tests/view_guard.zd places the receivers no C function the check
# reads takes, and each of its entries must still place one.
view-guard: bin/zc
	@bin/zc natives check views --system lib/system --emitter src/zemitterc.z --register tests/view_guard.zd

# fallback-guard -- the emitter never degrades silently: no example or driver C
# carries a live `/* zemitterc: */` marker (the emitter quoting itself in a string
# is not live), and the emitFail and marker-site counts in src/zemitterc.z are
# ratchets. A new refusal leg may raise EMITFAIL_BASELINE (say so in the commit);
# a new fallback leg may not.
EMITFAIL_BASELINE := 54
MARKER_BASELINE := 23
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

# native-guard -- the rows zc carries agree with the standard library's
# declarations (`zc natives check rows`, src/znativecheck.z): every native
# free function of a unit with rows has a row of its key; every z_@Name@,
# @ARM_canon.arm@ and @MEMBER_canon.name@ hole a text spells names a declared
# type, arm or member, or one the emitter binds; a plain hole the emitter does
# not bind and system does not declare is declared by one unit at most (the
# emitter looks it up in unit order); no text spells a tag constant or a
# generated struct's member literally; a value row's body calls nothing and
# opens no statement expression, and a constant variant is a value exactly when
# its plain row is; every native operator has a row in its unit and every
# operator row implements one; every conversion between two numeric types is
# declared native, answers a resultval exactly when it can fail, and has the
# row the numeric rule gives (body, needs and form; `zc natives conversions` prints them).
native-guard: bin/zc
	@bin/zc natives check rows --system lib/system

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
# with no flags from a foreign cwd, says why when it cannot, reports a dependency's error at that file, reports a generic body once,
# lints a subunit as itself, places L003/L011 only in the --full tier, and
# reports A012 for a file unit with no public block, never for a subunit.
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
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full --src $$d $(CURDIR)/$(ZLFULL_FIX) 2>&1); rc=$$?; \
	if [ $$rc -eq 0 ] || ! printf '%s\n' "$$out" | grep -q "did not run: unit 'tiered' is under none"; then \
	  echo "zl-full-guard FAIL: a unit under no --src root was not reported (rc=$$rc):"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_DEP) 2>&1); \
	if ! printf '%s\n' "$$out" | grep -qE 'depunit[/\\]dep\.z:3:10' || ! printf '%s\n' "$$out" | grep -q 'bad: "x" + n'; then \
	  echo "zl-full-guard FAIL: an error in a dependency unit was not shown at its own path and line:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_ONCE) 2>&1); \
	if [ "$$(printf '%s\n' "$$out" | grep -c 'L013')" != 1 ] || [ "$$(printf '%s\n' "$$out" | grep -c 'L022')" != 1 ]; then \
	  echo "zl-full-guard FAIL: a generic body's L013/L022 was not reported exactly once:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full $(CURDIR)/$(ZLFULL_SUB)/inner.z 2>&1); \
	if ! printf '%s\n' "$$out" | grep -q 'L013' || ! printf '%s\n' "$$out" | grep -q 'subunit/host/inner.z:4:5' || ! printf '%s\n' "$$out" | grep -q 'subunit/host/inner.z:11:14' || printf '%s\n' "$$out" | grep -q 'error\['; then \
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
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full --arch $(CURDIR)/$(ZLFULL_SUB).z 2>&1); \
	if [ "$$(printf '%s\n' "$$out" | grep -c 'A012')" != 1 ]; then \
	  echo "zl-full-guard FAIL: A012 did not report the unit with no public block once:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	out=$$(cd $$d && $(CURDIR)/bin/zl lint --full --arch $(CURDIR)/$(ZLFULL_SUB)/inner.z $(CURDIR)/$(ZLFULL_PUB) 2>&1); \
	if printf '%s\n' "$$out" | grep -q 'A012'; then \
	  echo "zl-full-guard FAIL: A012 reported a subunit, or a unit that has a public block:"; \
	  printf '%s\n' "$$out" | sed 's/^/    /'; fail=1; fi; \
	rm -rf $$d; \
	if [ $$fail -ne 0 ]; then exit 1; fi; \
	echo "zl-full-guard OK: --full runs without flags from a foreign cwd, says why when it cannot, places a dependency's error in its own file, and lints a subunit as itself"

