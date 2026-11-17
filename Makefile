CC       := gcc
# a program's main stack, in bytes: what Linux gives the main thread and a
# zerolang thread gets; zc reserves it for every program it builds for Windows
# (zcc.mainStackBytes), and the drivers here do the same there
MAINSTACK_BYTES := 8388608
# Windows (MSYS2 make, UCRT64 gcc) is told apart by the OS variable Windows sets
# for every process. There the drivers link everything statically, as zc links
# a program built for Windows, so bin/zc.exe runs with no DLL beside it, and
# reserve an 8 MiB main stack, as zc does for every program it builds for
# Windows (zcc.mainStackArgs; Windows' own default is 1 MiB); MAINSTACK and
# TCC_MAINSTACK spell it for gcc and tcc. They need no pthread (the thread shim
# is native Win32 there) and no libm (the C runtime holds the math functions;
# tcc has no libm to name).
ifeq ($(OS),Windows_NT)
WINDOWS_HOST := 1
MAINSTACK    := -Wl,--stack,$(MAINSTACK_BYTES)
TCC_MAINSTACK := -Wl,-stack=$(MAINSTACK_BYTES)
DRIVERLINK   := -static $(MAINSTACK)
THREADLIB    :=
LIBM         :=
EXE          := .exe
HOST_TARGET  := x86_64-w64-mingw32
HOST_OSNAME  := windows
else
WINDOWS_HOST :=
MAINSTACK    :=
TCC_MAINSTACK :=
DRIVERLINK   :=
THREADLIB    := -lpthread
LIBM         := -lm
EXE          :=
HOST_TARGET  := x86_64-linux
HOST_OSNAME  := linux
endif
# the gcc spelling of the discarded-qualifiers error; clang names it differently.
QUALWERR := $(if $(findstring clang,$(shell $(CC) --version 2>/dev/null | head -1)),-Werror=incompatible-pointer-types-discards-qualifiers,-Werror=discarded-qualifiers)
# The emitted C writes only what `main` reaches (make emitted-check):
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
# ZLINKOF -- what the emitted C file $(1) links, as `zc cobj $(1) --link`
# prints it: the objects of the C sources its `zcsrc:` lines name, built by the
# compiler that links, $(2) or $(CC), with the sanitizer flags $(3) (a sanitizer
# sees only what it instrumented), then the libraries its `zlink:` line
# declares, as that compiler names them (`-lX`, cl's `X.lib`). $(4) is the zc
# asked, bin/zc unless named: test-bootstrap asks each stage's seed. Every rule
# linking emitted C uses ZLINKOF, but the seed's own two links (SEEDLINK).
ZLINKOF = $$($(or $(4),bin/zc) cobj $(1) --link --cc $(or $(2),$(CC)) $(if $(3),--cflags "$(3)"))
# SEEDLINK -- the libraries of the seed's `zlink:` line, written here: the seed
# links before any zc exists to read it. The seed is emitted for Linux (dl, for
# the in-process tcc); on Windows it links no library, there being no
# in-process tcc there.
SEEDLINK := $(if $(WINDOWS_HOST),,-ldl)
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

.PHONY: all check complexity-report style-lint-fast style-lint test doc-check arch-check emitted-check ci ci-gates ci-legs gate-guards gate-same-c ci-corpus fuzz fuzz-ci fuzz-known \
	test-math-arm64 test-sqlite-arm64 test-mingw test-tcc-mingw test-msvc-wine test-msvc-corpus ci-windows windows-guest windows-due test-math-noasm test-portable-scalars math-asm-guard \
	bench-math bench-math-kernels test-clang test-tcc test-tcc-heavy readable-check build \
	tcc zc zl zls regen-goldens regen-fmt-goldens regen-matrix matrix-guard regen-constparity constparity-guard regen-math-asm \
	fmt-raw-guard regen-lsp-goldens bump-seed test-bootstrap install docs warn-check pedantic-check perf \
	perf-strict pre-push perf-elision \
	shim-guard thread-tsan-guard sqlite-decls-guard mode-parity \
	view-guard clean native-guard \
	
# Keep the per-example .c intermediates.
.SECONDARY:

# The zerolang sources of the tools under tools/ (one program per directory).
# the tools' sources: each tool's files, and the subunits of a tool's unit
# (tools/<tool>/<unit>/*.z)
TOOLSRC := $(wildcard tools/*/*.z tools/*/*/*.z)
TOOLDIRS := $(sort $(patsubst %/,%,$(dir $(wildcard tools/*/*.z))))

# SRC_SUBUNITS -- the subunit files of the compiler's units: the `.z` files of a
# folder under src/, which is the folder of the unit of its name. CHECKER_SRCS
# and EMITTER_SRCS are the two units written that way: every guard that reads
# one of them by its text reads the unit's file and its subunit files.
SRC_SUBUNITS := $(wildcard src/*/*.z)
CHECKER_SRCS := src/ztypecheck.z $(wildcard src/ztypecheck/*.z)
EMITTER_SRCS := src/zemitterc.z $(wildcard src/zemitterc/*.z)

# What the parse-tier linter reads.
ZLSCOPE := src/*.z $(SRC_SUBUNITS) lib/system/*.z lib/system/system/*.z lib/system/math/*.z tests/unit/*.z tests/unit/matrixgen/*.z $(TOOLSRC)
# What the --full tier reads. It checks a file as the unit it is, so style-lint
# gives each tree its own roots.
ZLFULLSCOPE := src/*.z $(SRC_SUBUNITS) lib/system/*.z lib/system/system/*.z lib/system/math/*.z
# What the formatter checks; tests/fixtures/ is input and stays as written.
FMTSCOPE := src/*.z $(SRC_SUBUNITS) lib/system/*.z lib/system/system/*.z lib/system/math/*.z examples/*.z tests/unit/*.z tests/unit/matrixgen/*.z $(TOOLSRC) lib/system/*.zd project.zd

all: bin/zc bin/zl bin/zls

# check -- the fast pre-commit gate.
check: style-lint-fast complexity-report

# complexity-report -- every function over A008's threshold, highest first, into
# $(COMPLEXITY_TSV); the per-file ratchet is tests/fixtures/arch_baseline.txt.
COMPLEXITY_SCOPE := src/*.z $(SRC_SUBUNITS) lib/system/*.z lib/system/system/*.z lib/system/math/*.z tests/unit/*.z tests/unit/matrixgen/*.z examples/*.z $(TOOLSRC)
COMPLEXITY_TSV := $(BUILDDIR)/cognitive-complexity.tsv
complexity-report: bin/zl
	@mkdir -p $(BUILDDIR)
	@bin/zl lint --complexity 15 --complexity-table $(COMPLEXITY_TSV) $(COMPLEXITY_SCOPE)

# style-lint-fast -- the parse-tier lint and the formatter check (docs/zl.pdoc).
# style-lint -- adds the typecheck tier, which needs each tree's roots.
style-lint-fast: bin/zl
	bin/zl lint $(ZLSCOPE)
	bin/zl format --check $(FMTSCOPE)

style-lint: bin/zl
	bin/zl lint --full --src src --system lib/system $(ZLFULLSCOPE)
	bin/zl lint --full --src tests/unit --src src --system lib/system tests/unit/*.z
	@for d in $(TOOLDIRS); do echo "bin/zl lint --full --src $$d --src src --system lib/system $$d/*.z"; \
	  bin/zl lint --full --src $$d --src src --system lib/system $$d/*.z $(filter $$d/%,$(wildcard tools/*/*/*.z)) || exit 1; done
	bin/zl format --check $(FMTSCOPE)

# out/ztestrunner -- the corpus runner (src/ztestrunner.z and its subunits under
# src/ztestrunner/, and src/zdocblocks.z for the doccode kind).
$(BUILDDIR)/ztestrunner: bin/zc src/ztestrunner.z $(wildcard src/ztestrunner/*.z) src/zjobs.z src/zdocblocks.z src/zcc.z src/ztarget.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc ztestrunner --src src --system lib/system --emit-c $(BUILDDIR)/ztestrunner.c
	$(CC) $(CFLAGS) $(MAINSTACK) -o $(BUILDDIR)/ztestrunner $(BUILDDIR)/ztestrunner.c $(call ZLINKOF,$(BUILDDIR)/ztestrunner.c) -lm

# bin/zgate -- the gate tool (tools/zgate): snapshots, C diffs, the allocation
# split, pregate, the per-batch gate and the CI timings. A development program:
# `make install` leaves it out. It reads the job pool and the compiler
# families from src (zjobs, zcc).
bin/zgate: bin/zc $(wildcard tools/zgate/*.z tools/zgate/*/*.z) src/zjobs.z src/zcc.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR) bin
	bin/zc zgate --src tools/zgate --src src --system lib/system --emit-c $(BUILDDIR)/zgate.c
	$(CC) $(CFLAGS) -o bin/zgate $(BUILDDIR)/zgate.c $(call ZLINKOF,$(BUILDDIR)/zgate.c) -lm

# test -- the fast corpus gate. The arch and docs kinds run bin/zl, so it must be
# built: without it those kinds count nothing and every ratchet reads zero. The
# usage kind runs each driver from bin/, bin/zls and bin/zgate among them, and
# one of its cases links with the vendored tcc. ci-corpus, test-clang and
# test-tcc run the usage kind too, so they build the same drivers.
test: bin/zc bin/zl bin/zls bin/zgate $(BUILDDIR)/ztestrunner $(BUILDDIR)/tcc
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC)

# doc-check -- the doccode kind alone: every zerolang example in docs/*.pdoc and
# README.md emits with `zc emit --eager` (or fails as its expect= says) and is
# laid out as zl fmt lays it, with check=none and unformatted blocks ratcheted in
# tests/fixtures/doccode_baseline.txt. `make test` runs it too.
doc-check: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --only doccode

# arch-check -- the arch kind alone: `zl lint --full --arch --counts` over the
# compiler and front-end units against tests/fixtures/arch_baseline.txt, in
# seconds. `make test` runs it too.
arch-check: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --only arch

# emitted-check -- the emitted kind alone (src/ztestrunner/emitted.z): every
# example and corpus program is emitted once for each variant a check reads,
# in parallel, and the checks read that C: no unused file-scope item and no
# undeclared call under gcc, clang and mingw-w64 in each configuration the C
# branches on, no unreachable statement under clang, no top-level C item
# unreached from main (creach), no type both untagged and forward-declared, no
# mono labelled by an alias, C that compiles under --eager (programs, user
# units, lib/system, src), and the programs that declare a link library or
# that --cc tcc refuses, per system, at their baselines. The baselines and the
# known-bad lists are src/ztestrunner/expected.z's.
emitted-check: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --only emitted

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
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --only agree

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
	@echo "CI GATE GREEN: perf-strict + ci-gates (guards, toolchains, heavy corpus) + bootstrap"
	@$(MAKE) --no-print-directory windows-due

# GATE_GUARDS -- the legs of ci-gates the per-batch gate runs, in parallel and to
# the end (`make -k -j gate-guards`). The gate runs check, test, perf-strict and
# the clang and tcc corpora itself.
GATE_GUARDS := style-lint warn-check pedantic-check native-guard view-guard emitted-check \
	shim-guard thread-tsan-guard sqlite-decls-guard \
	fmt-raw-guard nativec-agree-guard prelude-share-guard \
	math-asm-guard matrix-guard constparity-guard test-math-noasm test-portable-scalars \
	test-msvc-wine
# GATE_SAME_C -- the legs that only rebuild and run the emitted C of the run
# corpus: the per-batch gate leaves them out when the C every program emits is the
# C its snapshot's compiler emits, and runs them otherwise.
GATE_SAME_C := test-ub test-mingw test-tcc-mingw test-msvc-corpus

gate-guards: $(GATE_GUARDS)

gate-same-c: $(GATE_SAME_C)

# ci-gates -- the gates ci runs in parallel, after perf-strict: the per-batch
# gate's legs and the slower corpus runs only ci makes.
CI_ONLY := complexity-report readable-check test-tcc-heavy test-clang \
	test-o2 test-math-arm64 test-sqlite-arm64 mode-parity fuzz-ci ci-corpus
ci-gates: $(GATE_GUARDS) $(GATE_SAME_C) $(CI_ONLY)

# ci-legs -- ci-gates' legs on one line, which `zgate time gates` runs one at a
# time
ci-legs:
	@echo $(GATE_GUARDS) $(GATE_SAME_C) $(CI_ONLY)

ci-corpus: bin/zc bin/zl bin/zls bin/zgate $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --heavy --jobs $(NPROC)

# test-math-arm64 -- the math corpus cross-built for aarch64 and run under qemu
# against the same goldens, on the builtin word primitives (-DZ_MATH_NOASM) and
# on the generated asm kernels. The cases tests/arm64-known-failures.txt lists
# are refused for aarch64 (f128, which only x86-64 has): any other outcome of
# theirs fails. Skipped without the cross tools.
ARM64_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --cc aarch64-linux-gnu-gcc --target aarch64-linux-gnu \
  --exec "qemu-aarch64 -L /usr/aarch64-linux-gnu" --only run --known tests/arm64-known-failures.txt \
  --root . --jobs $(NPROC)
ARM64_TOOLS = command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 && command -v qemu-aarch64 >/dev/null 2>&1
test-math-arm64: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(ARM64_TOOLS) || { echo "test-math-arm64: skipped (needs aarch64-linux-gnu-gcc and qemu-aarch64)"; exit 0; }; \
	$(ARM64_RUN) --cases tests/fixtures/emitc_corpus/math > $(BUILDDIR)/math-arm64.log 2>&1 \
	  || { cat $(BUILDDIR)/math-arm64.log; echo "test-math-arm64 FAIL (asm): a math program on aarch64 differs from its golden or is not refused as listed"; exit 1; }; \
	$(ARM64_RUN) --cases tests/fixtures/emitc_corpus/math --ccflags -DZ_MATH_NOASM > $(BUILDDIR)/math-arm64.log 2>&1 \
	  || { cat $(BUILDDIR)/math-arm64.log; echo "test-math-arm64 FAIL (builtins): a math program on aarch64 differs from its golden or is not refused as listed"; exit 1; }; \
	echo "test-math-arm64 OK: the math corpus on aarch64 (asm and builtins) matches its goldens, f128 refused as listed"

# test-sqlite-arm64 -- the sqlite programs (sqlite_*) cross-built for aarch64,
# the vendored SQLite among their C sources, and run under qemu against their
# goldens. Skipped without the cross tools.
test-sqlite-arm64: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(ARM64_TOOLS) || { echo "test-sqlite-arm64: skipped (needs aarch64-linux-gnu-gcc and qemu-aarch64)"; exit 0; }; \
	$(ARM64_RUN) --cases sqlite_ > $(BUILDDIR)/sqlite-arm64.log 2>&1 \
	  || { cat $(BUILDDIR)/sqlite-arm64.log; echo "test-sqlite-arm64 FAIL: a sqlite program on aarch64 differs from its golden"; exit 1; }; \
	echo "test-sqlite-arm64 OK: the sqlite programs built for aarch64 match their goldens"

# the wine prefix the Windows legs share (shim-guard, test-mingw), created
# once and alone: wines that start in parallel on a missing prefix race to
# create it. Without wine there is nothing to create and no stamp is left.
WINE_PREFIX := $(abspath $(BUILDDIR))/wineprefix
WINE_ENV := DISPLAY= WINEDEBUG=-all WINEPREFIX=$(WINE_PREFIX)
# WINE_SERVER_UP -- a shell command that starts the prefix's wine server, to
# stay 30 seconds after its last client. A leg runs it before its first Windows
# program. Left to itself, wine starts a server with the first client and the
# server stops moments after the last one. A leg that runs many short programs
# has such moments, and a program that starts while the server stops fails
# with no output ("wine client error: recvmsg: Connection reset by peer"). A
# server that is up already answers 2, which is not a failure. Where
# wineserver is not on PATH it sits beside wine's own libraries.
WINE_SERVER_UP = { ws=$$(command -v wineserver || ls /usr/lib/*/wine/wineserver /usr/lib*/wine/wineserver 2> /dev/null | head -1); \
  if [ -n "$$ws" ]; then $(WINE_ENV) "$$ws" -p30 > /dev/null 2>&1 || true; \
  else echo "note: no wineserver found, so wine stops and starts its own: a Windows program may fail to start"; fi; }
$(BUILDDIR)/wineprefix/.ready:
	@if command -v wine >/dev/null 2>&1; then \
	  mkdir -p $(WINE_PREFIX) && $(WINE_SERVER_UP) && $(WINE_ENV) timeout 300 wineboot -i > /dev/null 2>&1 && touch $@; \
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
	$(WINE_SERVER_UP); \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS)" --target x86_64-w64-mingw32 \
	   --exec wine --only run --known $(MINGW_KNOWN) --root . --jobs $(NPROC) > $(BUILDDIR)/mingw-run.log 2>&1 \
	  || { cat $(BUILDDIR)/mingw-run.log; echo "test-mingw FAIL: the Windows failure set moved (above): a new failure is a regression; a listed one that passes now is deleted from $(MINGW_KNOWN) in the same commit"; exit 1; }; \
	echo "test-mingw OK: the RUN cases pass on Windows (mingw-w64, wine) but for $(MINGW_KNOWN)'s, none new"

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
	$(WINE_SERVER_UP); \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(BUILDDIR)/x86_64-win32-tcc --cc-forward \
	   --ccflags "-B $(TCCW_LIB)" --target x86_64-w64-mingw32 \
	   --exec wine --only run --known $(TCC_MINGW_KNOWN) --root . --jobs $(NPROC) > $(BUILDDIR)/tcc-mingw-run.log 2>&1 \
	  || { cat $(BUILDDIR)/tcc-mingw-run.log; echo "test-tcc-mingw FAIL: the Windows tcc failure set moved (above): a new failure is a regression; a listed one that passes now is deleted from $(TCC_MINGW_KNOWN) in the same commit"; exit 1; }; \
	echo "test-tcc-mingw OK: the RUN cases pass on Windows (tcc, wine) but for $(TCC_MINGW_KNOWN)'s, none new"

# test-msvc-wine -- MSVC itself, cl.exe run under wine (msvc-wine; MSVC_WINE
# names its bin/x64 directory): the runner's ctest kind builds the thread
# shim's test with cl and runs it under wine, and the run kind has zc build
# sqlite_hello with cl (its C objects included) and judges it against its
# golden. That zc refuses cl for a target that is not Windows is a usage case
# (zc_cl_refuses_linux_target), in make test. Skipped without MSVC_WINE or
# wine.
MSVC_WINE ?=
test-msvc-wine: bin/zc $(BUILDDIR)/ztestrunner $(BUILDDIR)/wineprefix/.ready $(NATIVEC_INC)/zthread_shim.inc
	@if [ -n "$(WINDOWS_HOST)" ]; then \
	  echo "test-msvc-wine: skipped (Linux only: it runs MSVC under wine; on Windows windows-guest builds with cl itself)"; exit 0; fi; \
	if [ -z "$(MSVC_WINE)" ] || [ ! -x "$(MSVC_WINE)/cl" ] || ! command -v wine > /dev/null 2>&1; then \
	  echo "test-msvc-wine: skipped (needs wine and MSVC_WINE=<dir>/msvc/bin/x64 from tools/windows/msvc-wine.sh; see tools/windows/README.md)"; exit 0; fi; \
	l=$(BUILDDIR)/msvc-wine.log; \
	$(WINE_SERVER_UP); \
	{ $(WINE_ENV) $(SHIM_RUN) --cc $(MSVC_WINE)/cl --ccflags "$(SHIM_INC)" --exec wine --timeout 300 \
	  && $(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --root . --only run --zc-build --cc $(MSVC_WINE)/cl \
	       --target x86_64-pc-windows-msvc --exec wine --timeout 300 --cases sqlite_hello; } > $$l 2>&1 \
	  || { cat $$l; echo "test-msvc-wine FAIL (above)"; exit 1; }; \
	echo "test-msvc-wine OK: MSVC built the thread shim's test, which passes under wine, and zc build --cc cl builds sqlite_hello (C objects included) to its golden"

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
	[ -n "$(WINDOWS_HOST)" ] || $(WINE_SERVER_UP); \
	$(MSVC_CORPUS_RUN) --only run --known $(MSVC_KNOWN) --root . --jobs $(NPROC) > $(BUILDDIR)/msvc-run.log 2>&1 \
	  || { cat $(BUILDDIR)/msvc-run.log; echo "test-msvc-corpus FAIL: the MSVC failure set moved (above): a new failure is a regression; a listed one that passes now is deleted from $(MSVC_KNOWN) in the same commit"; exit 1; }; \
	echo "test-msvc-corpus OK: the RUN cases pass on Windows (MSVC, $(MSVC_CORPUS_WHERE)) but for $(MSVC_KNOWN)'s, none new"

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
	$(WINE_SERVER_UP); \
	$(WINE_ENV) $(BUILDDIR)/ztestrunner --zc bin/zc --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS)" --target x86_64-w64-mingw32 \
	   --exec wine --only run --root . --jobs $(NPROC) --save-temps --tmp $(abspath $(WINDOWS_DIR))/build \
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
WINDOWS_PATHS := src/zemitterc.z src/zemitterc src/zc.z src/zcobj.z src/zcheck.z src/ztestrunner.z \
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
test-math-noasm: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --ccflags -DZ_MATH_NOASM --only run \
	   --cases tests/fixtures/emitc_corpus/math --root . --jobs $(NPROC) > $(BUILDDIR)/math-noasm.log 2>&1 \
	  || { cat $(BUILDDIR)/math-noasm.log; echo "test-math-noasm FAIL: a math program on the C kernels differs from its golden"; exit 1; }; \
	echo "test-math-noasm OK: the math corpus on math's C kernels matches its goldens"

# test-portable-scalars -- every run case whose source names i128, u128 or f16,
# built on their struct forms (what tcc always takes) and compared with the
# native goldens.
test-portable-scalars: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --ccflags "-DZ_INT128_PORTABLE -DZ_F16_PORTABLE" --only run \
	   --cases-naming i128 --cases-naming u128 --cases-naming f16 --root . --jobs $(NPROC) > $(BUILDDIR)/portable-scalars.log 2>&1 \
	  || { cat $(BUILDDIR)/portable-scalars.log; echo "test-portable-scalars FAIL: a program on the struct forms of i128, u128 and f16 differs from its golden"; exit 1; }; \
	echo "test-portable-scalars OK: the programs naming i128, u128 or f16 match their goldens on the struct forms"

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
test-clang: bin/zc bin/zl bin/zls bin/zgate $(BUILDDIR)/ztestrunner
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc clang --cc-forward --root . --jobs $(NPROC)

# test-ub -- the run corpus with C's undefined behaviour trapping: every check
# of UBSan, without -fwrapv. The language promises two's-complement wrapping,
# and MSVC has no -fwrapv, so the emitted C and the runtime must never reach
# undefined signed arithmetic; nor may they pass a null pointer to a library
# call, read through a misaligned pointer or index past an array. A report
# aborts its case. Linux only: the Windows toolchains have no UBSan runtime.
UB_FLAGS := -fsanitize=undefined -fno-sanitize-recover=all -fno-wrapv
ifneq ($(WINDOWS_HOST),)
test-ub:
	@echo "test-ub: skipped (Linux only: UCRT64 gcc has no UBSan runtime; the Linux run covers the same C)"
else
test-ub: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc gcc --ccflags "$(UB_FLAGS)" --only run --root . \
	   --jobs $(NPROC) > $(BUILDDIR)/ub.log 2>&1 \
	  || { cat $(BUILDDIR)/ub.log; echo "test-ub FAIL: a run case reached C's undefined behaviour (UBSan; log $(BUILDDIR)/ub.log)"; exit 1; }; \
	echo "test-ub OK: the run cases reach no undefined behaviour"

endif

# test-o2 -- the run corpus built at -O2 under clang. The other runs build
# without optimisation, and an optimiser acts on what the C leaves undefined: a
# case that passes there and fails here shows such a place. gcc's optimiser is
# not run over the corpus, to keep the leg short: the drivers are gcc -O2 builds
# of the largest emitted program.
test-o2: bin/zc bin/zl $(BUILDDIR)/ztestrunner
	@command -v clang >/dev/null 2>&1 || { echo "test-o2: clang not installed, skipped"; exit 0; }; \
	$(BUILDDIR)/ztestrunner --zc bin/zc --cc clang --ccflags "-O2" --only run --root . \
	   --jobs $(NPROC) > $(BUILDDIR)/o2-clang.log 2>&1 \
	  || { cat $(BUILDDIR)/o2-clang.log; echo "test-o2 FAIL: a run case differs at -O2 under clang (log $(BUILDDIR)/o2-clang.log)"; exit 1; }; \
	echo "test-o2 OK: the run cases at -O2 under clang"

# what a corpus run under the vendored tcc needs built first.
TCC_RUN_DEPS := bin/zc bin/zl bin/zls bin/zgate $(BUILDDIR)/tcc $(BUILDDIR)/ztestrunner

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
	   --ccflags "-B $(TCCLIB)" --root . $(TCC_TIER) --jobs $(NPROC) --known $(TCC_KNOWN) \
	   > $(BUILDDIR)/tcc-run.log 2>&1 \
	  || { cat $(BUILDDIR)/tcc-run.log; echo "test-tcc FAIL: the tcc failure set moved (above): a new failure is a regression; a listed one that passes now, or moves from (cc) to (zc) because a unit guards itself, is changed in $(TCC_KNOWN) in the same commit"; exit 1; }; \
	echo "test-tcc$(TCC_TIER:--heavy=-heavy) OK: the corpus passes under tcc but for $(TCC_KNOWN)'s, none new"

# test-tcc-heavy -- test-tcc with the self-host and fixpoint kinds (ci only).
# It names test-tcc's prerequisites itself so the parent make builds them before
# the sub-make starts; otherwise both link bin/zc at once.
test-tcc-heavy: $(TCC_RUN_DEPS)
	@$(MAKE) --no-print-directory test-tcc TCC_TIER=--heavy

# readable-check -- --readable-names programs build and behave as their goldens
# say: the run kind builds the cases about naming (shadowing, case-only arm
# names, C-keyword members, locals named as system-header macros) with
# --readable-names. A readable-named compiler must emit identical C, and with
# mingw-w64 present the readable C of the macro case and of the compiler must
# compile for Windows, whose headers define macros Linux's do not (`near`,
# `far`, `FAR`).
READABLE_CASES := hello vector records fibonacci typedefs shadow_unit_const rn_sibling_shadow \
  rn_macro_names rn_two_companions arm_case_tags tag_stem_clash member_names_c_words generator_field_names
readable-check: bin/zc $(BUILDDIR)/buildstamp.o $(BUILDDIR)/ztestrunner
	@mkdir -p $(BUILDDIR)/rn
	@$(BUILDDIR)/ztestrunner --zc bin/zc --cc $(CC) --root . --jobs $(NPROC) --only run --zc-flag --readable-names \
	   $(addprefix --cases ,$(READABLE_CASES)) > $(BUILDDIR)/rn/cases.log 2>&1 \
	  || { cat $(BUILDDIR)/rn/cases.log; echo "readable-check FAIL: a program built with --readable-names differs from its golden (above)"; exit 1; }
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
	      || { cat $(BUILDDIR)/rn/$$n-win.log; echo "readable-check FAIL: $$n's readable C does not compile for Windows (above)"; exit 1; }; \
	  done; \
	fi
	@echo "readable-check OK: the naming cases built with --readable-names match their goldens, and the readable-named compiler emits the same C"

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
	$(CC) $(CFLAGS) -o $@ bootstrap/zc.c $(if $(WINDOWS_HOST),$(DRIVERLINK)) $(SEEDLINK) -lm

# NATIVEC_DEP -- the files the generated `nativec` unit is made from (znativec.servedText):
# zc serves them to its own build, so a change to one rebuilds bin/zc
NATIVEC_DEP := lib/system/runtime.zd $(wildcard lib/system/*/nativec.zd lib/system/*/nativec/*.zd)

# the C of rows zc carries, written out for a C test to include (NATIVEC_INC)
$(NATIVEC_INC)/zthread_shim.inc: bin/zc
	@mkdir -p $(NATIVEC_INC)
	bin/zc natives show prelude.@zthread --c --system lib/system > $@

# bin/zc -- the self-hosted compiler, built by the seed.
bin/zc.c: $(wildcard src/*.z) $(SRC_SUBUNITS) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) $(NATIVEC_DEP) $(ZC)
	@mkdir -p bin
	$(ZC) zc --src src --system lib/system $(ZCHASH) --emit-c bin/zc.c

$(BUILDDIR)/zc.o: bin/zc.c
	@mkdir -p $(BUILDDIR)
	$(CC) $(CFLAGS) $(OPTFLAGS) $(STAMPDEF) -c bin/zc.c -o $@

bin/zc: $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) $(DRIVERLINK) -o bin/zc $(BUILDDIR)/zc.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,bin/zc.c,,,$(ZC)) $(THREADLIB) -lm

zc: bin/zc

# bin/zl -- the linter and formatter (src/zl.z); no emitter.
out/zl.c: $(BUILDDIR)/zc.o $(SRC_SUBUNITS) $(wildcard src/zl.z) $(wildcard src/zsource.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) | bin/zc
	@mkdir -p out
	bin/zc zl --src src --system lib/system $(ZCHASH) --emit-c out/zl.c

$(BUILDDIR)/zl.o: out/zl.c
	$(CC) $(CFLAGS) $(OPTFLAGS) $(STAMPDEF) -c out/zl.c -o $@

bin/zl: $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ)
	@mkdir -p bin
	$(CC) $(DRIVERLINK) -o bin/zl $(BUILDDIR)/zl.o $(BUILDDIR)/buildstamp.o $(MIMALLOC_OBJ) $(call ZLINKOF,$(BUILDDIR)/zl.c) $(THREADLIB) -lm

# bin/zls -- the language server (src/zls.z); no emitter.
out/zls.c: $(BUILDDIR)/zc.o $(SRC_SUBUNITS) $(wildcard src/zls.z) $(wildcard src/zcheck.z) $(wildcard src/ztarget.z) $(wildcard src/zproject.z) $(wildcard src/zsource.z) $(wildcard src/zdiag.z) $(wildcard src/zrule.z) $(wildcard src/zfix.z) $(wildcard src/ztypecheck.z) $(wildcard src/ztypes.z) $(wildcard src/zenv.z) $(wildcard src/ztyping.z) $(wildcard src/zgenerator.z) $(wildcard src/zfmt.z) $(wildcard src/zfmtcursor.z) $(wildcard src/zdoc.z) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z) | bin/zc
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

# fuzz -- the token-mutation fuzzer (tests/unit/zfuzz.z) against an
# AddressSanitizer build of the compiler: N mutants of the example and corpus
# programs, decided by SEED. A mutant must build or be refused with a
# diagnostic; a crash, a hang, a panic, an emitter error or C the C compiler
# refuses is a finding, kept in out/fuzz/findings with zc's stderr beside it.
# `make fuzz SEED=7 N=100000` for a longer run.
SEED ?= 1
N ?= 20000
FUZZ_SAN := -fsanitize=address,undefined -fno-sanitize-recover=undefined
out/zc-asan: bin/zc $(wildcard src/*.z lib/system/*.z lib/system/system/*) $(SRC_SUBUNITS)
	@mkdir -p $(BUILDDIR)
	bin/zc build src/zc.z --system lib/system -o $@ --cflags "-O1 -g -fno-omit-frame-pointer $(FUZZ_SAN)" --ldflags "$(FUZZ_SAN)"

out/zfuzz: bin/zc tests/unit/zfuzz.z $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc build tests/unit/zfuzz.z --system lib/system -o $@

fuzz: out/zc-asan out/zfuzz
	@rm -rf $(BUILDDIR)/fuzz
	out/zfuzz --zc $(CURDIR)/out/zc-asan --system $(CURDIR)/lib/system \
	  --corpus $(CURDIR)/examples,$(CURDIR)/tests/fixtures/emitc_corpus \
	  --work $(CURDIR)/$(BUILDDIR)/fuzz --seed $(SEED) --count $(N)

# fuzz-ci -- the fixed-seed fuzz run of `make ci`. It fails on a finding whose
# signature tests/fuzz-known-findings.txt does not list, and on a listed one
# the run no longer shows. `make fuzz-known` writes that file from the run.
FUZZ_CI_SEED := 11
FUZZ_CI_N := 2500
FUZZ_KNOWN := tests/fuzz-known-findings.txt
FUZZ_CI_RUN = out/zfuzz --zc $(CURDIR)/out/zc-asan --system $(CURDIR)/lib/system \
	  --corpus $(CURDIR)/examples,$(CURDIR)/tests/fixtures/emitc_corpus \
	  --work $(CURDIR)/$(BUILDDIR)/fuzz-ci --seed $(FUZZ_CI_SEED) --count $(FUZZ_CI_N) \
	  --known $(FUZZ_KNOWN)
ifdef WINDOWS_HOST
fuzz-ci:
	@echo "fuzz-ci: skipped (Linux only: it fuzzes a compiler built with AddressSanitizer, which no UCRT64 gcc has; the Linux run fuzzes the same source)"
else
fuzz-ci: out/zc-asan out/zfuzz
	@rm -rf $(BUILDDIR)/fuzz-ci
	$(FUZZ_CI_RUN)
endif

fuzz-known: out/zc-asan out/zfuzz
	@rm -rf $(BUILDDIR)/fuzz-ci
	$(FUZZ_CI_RUN) --write-known

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
out/matrixgen: bin/zc tests/unit/matrixgen.z $(wildcard tests/unit/matrixgen/*.z) $(wildcard src/*.z) $(SRC_SUBUNITS) $(wildcard lib/system/*.z) $(wildcard lib/system/system/*.z lib/system/math/*.z)
	@mkdir -p $(BUILDDIR)
	bin/zc matrixgen --src tests/unit --src src --system lib/system --emit-c $(BUILDDIR)/matrixgen.c
	$(CC) $(CFLAGS) -o $(BUILDDIR)/matrixgen $(BUILDDIR)/matrixgen.c $(call ZLINKOF,$(BUILDDIR)/matrixgen.c) -lm

# regen-matrix -- rewrite the matrix fixtures from tests/fixtures/matrix_expect.zd.
regen-matrix: out/matrixgen
	$(BUILDDIR)/matrixgen --root .

# matrix-guard -- the committed matrix fixtures are matrixgen's output, regenerated
# under a scratch root, with none left over that it no longer writes.
matrix-guard: out/matrixgen
	@d=$$(mktemp -d); mkdir -p $$d/tests/fixtures; \
	cp tests/fixtures/matrix_expect.zd tests/fixtures/run_cases.txt $$d/tests/fixtures/; \
	$(BUILDDIR)/matrixgen --root $$d > $$d/gen.log 2>&1 || { cat $$d/gen.log; rm -rf $$d; exit 1; }; \
	bad=0; n=0; \
	for f in $$(cd $$d && find tests/fixtures -type f ! -name matrix_expect.zd); do \
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
regen-lsp-goldens: bin/zc bin/zl bin/zls $(BUILDDIR)/ztestrunner
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
	  s=""; case "$$cc" in *tcc*) f="-B $(TCCLIB)"; l="$(TCC_MAINSTACK)";; \
	    cl|cl.exe|*/cl|*/cl.exe) f="$(MSVC_CFLAGS) -O2 -Fo:$(BUILDDIR)/"; l=""; m=1; o="-Fe:"; s="-link -STACK:$(MAINSTACK_BYTES)";; \
	    *) f="$(CFLAGS)"; l="$(DRIVERLINK)";; esac; \
	  if ! command -v $$cc >/dev/null 2>&1 && [ ! -x $$cc ]; then \
	    echo "test-bootstrap FAIL: '$$cc' is not available"; \
	    echo "  BOOTSTRAP_CCS names the toolchains this claim is proved against, so a"; \
	    echo "  missing one is a smaller claim, not a smaller run. Install it, or narrow"; \
	    echo "  the list deliberately: make test-bootstrap BOOTSTRAP_CCS='gcc'"; \
	    exit 1; \
	  fi; \
	  echo "== bootstrap chain: $$cc =="; \
	  $$cc $$f $${o}$(BUILDDIR)/zc-seed-$$tag bootstrap/zc.c $(if $(WINDOWS_HOST),$$l) $(SEEDLINK) $(LIBM) $$s; \
	  $(BUILDDIR)/zc-seed-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b1-$$tag.c; \
	  k="$(call ZLINKOF,$(BUILDDIR)/b1-$$tag.c,$$cc,,$(BUILDDIR)/zc-seed-$$tag)"; [ -n "$$m" ] || k="$$k $(LIBM)"; \
	  $$cc $$f $$l $${o}$(BUILDDIR)/zc-b1-$$tag $(BUILDDIR)/b1-$$tag.c $$k $$s; \
	  $(BUILDDIR)/zc-b1-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b2-$$tag.c; \
	  $$cc $$f $$l $${o}$(BUILDDIR)/zc-b2-$$tag $(BUILDDIR)/b2-$$tag.c $$k $$s; \
	  $(BUILDDIR)/zc-b2-$$tag zc --src src --system lib/system --emit-c $(BUILDDIR)/b3-$$tag.c; \
	  if cmp -s $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c; then \
	    echo "  fixpoint OK (b2 == b3) under $$tag"; \
	  else \
	    echo "FAIL: seed-built compiler does not converge under $$tag"; \
	    diff $(BUILDDIR)/b2-$$tag.c $(BUILDDIR)/b3-$$tag.c | head -6; exit 1; \
	  fi; \
	  $(BUILDDIR)/zc-b1-$$tag ztypes_smoke --src tests/unit --src src --system lib/system --emit-c $(BUILDDIR)/zt-$$tag.c; \
	  k="$(call ZLINKOF,$(BUILDDIR)/zt-$$tag.c,$$cc,,$(BUILDDIR)/zc-seed-$$tag)"; [ -n "$$m" ] || k="$$k $(LIBM)"; \
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
# each compiler actually implements (tcc silently accepts any -W). The twelve
# compiles are independent and run at once, each into its own log under
# $(BUILDDIR)/warn-check; every one that fails is reported.
WARN_CCS ?= $(CC) clang $(BUILDDIR)/tcc

WARNSET_gcc   := $(CFLAGS_BASE) -Werror=discarded-qualifiers $(OPTFLAGS)
WARNSET_clang := $(CFLAGS_BASE) -Werror=incompatible-pointer-types-discards-qualifiers $(OPTFLAGS)
WARNSET_tcc   := -B $(TCCLIB) -Wall -Wunsupported \
                 -Werror=implicit-function-declaration -Werror=discarded-qualifiers

warn-check: bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c $(BUILDDIR)/tcc
	@set -e; \
	for cc in $(WARN_CCS); do \
	  if ! command -v $$cc >/dev/null 2>&1 && [ ! -x $$cc ]; then \
	    echo "warn-check FAIL: '$$cc' is not available"; \
	    echo "  WARN_CCS names the compilers this gate speaks for; install it, or"; \
	    echo "  narrow the list deliberately: make warn-check WARN_CCS='gcc'"; \
	    exit 1; \
	  fi; \
	done; \
	mkdir -p $(BUILDDIR)/warn-check; \
	rm -f $(BUILDDIR)/warn-check/*.log $(BUILDDIR)/warn-check/*.fail; \
	i=0; \
	for cc in $(WARN_CCS); do \
	  tag=$${cc##*/}; \
	  case "$$tag" in \
	    *tcc*)   w='$(WARNSET_tcc)';; \
	    *clang*) w='$(WARNSET_clang)';; \
	    *)       w='$(WARNSET_gcc)';; \
	  esac; \
	  for f in bootstrap/zc.c bin/zc.c $(BUILDDIR)/zl.c $(BUILDDIR)/zls.c; do \
	    i=$$((i + 1)); n=$(BUILDDIR)/warn-check/$$tag-$$i; \
	    ( $$cc $$w -Werror -c $$f -o /dev/null > $$n.log 2>&1 || echo "$$tag on $$f" > $$n.fail ) & \
	  done; \
	done; \
	wait; \
	if ls $(BUILDDIR)/warn-check/*.fail > /dev/null 2>&1; then \
	  for x in $(BUILDDIR)/warn-check/*.fail; do echo "warn-check FAIL: $$(cat $$x)"; cat $${x%.fail}.log; done; \
	  exit 1; \
	fi; \
	for cc in $(WARN_CCS); do echo "  $${cc##*/}: clean on seed + zc + zl + zls"; done
	@echo "warn-check OK: zero warnings from $(words $(WARN_CCS)) compiler(s) on all four emitted files"

# pedantic-check -- the emitted C stays inside ISO C17, which is all MSVC
# compiles: the runner's pedantic kind, once for each target, reads the C of
# zc, zl and zls and of every RUN case with gcc -std=c17 -Wpedantic
# -fsyntax-only, and one diagnostic fails. Each target's C is read by a gcc FOR
# that target, since the runtime chooses its branches by the compiler's own
# _WIN32: on Linux the host gcc and the mingw-w64 cross gcc, on a Windows host
# UCRT64 gcc, where no compiler for Linux exists and that half is skipped.
# -Wpedantic does not see __typeof__, __builtin_* or __attribute__;
# test-msvc-corpus, cl itself, does.
PEDANTIC_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --root . --jobs $(NPROC) --only pedantic
ifeq ($(WINDOWS_HOST),)
PEDANTIC_LEGS = $(PEDANTIC_RUN) --cc gcc --target x86_64-linux-gnu \
  && $(PEDANTIC_RUN) --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS)" --target x86_64-w64-mingw32
PEDANTIC_WHERE := for Linux and for Windows
else
PEDANTIC_LEGS = $(PEDANTIC_RUN) --cc gcc --target x86_64-w64-mingw32
PEDANTIC_WHERE := for Windows (the Linux half is Linux only: no compiler for Linux on Windows)
endif
pedantic-check: bin/zc $(BUILDDIR)/ztestrunner
	@if [ -z "$(WINDOWS_HOST)" ] && ! $(MINGW_UCRT_CHECK); then \
	  echo "pedantic-check FAIL: the Windows C is read by $(MINGW_CC), which cannot target the UCRT: $(MINGW_UCRT_FIX); see tools/windows/README.md"; exit 1; fi; \
	{ $(PEDANTIC_LEGS); } > $(BUILDDIR)/pedantic.log 2>&1 \
	  || { cat $(BUILDDIR)/pedantic.log; echo "pedantic-check FAIL (above): emit the portable form of what -Wpedantic names"; exit 1; }; \
	echo "pedantic-check OK: the C of the drivers and the run cases has no -Wpedantic diagnostic, $(PEDANTIC_WHERE)"

# perf -- the self-compile's numbers (docs/testing.pdoc, Performance): line
# count, wall, peak RSS, phase split and the allocation total.
PERFARGS := zc --src src --system lib/system --emit-c /dev/null
PERFRUN  := $(PERFBIN) $(PERFARGS)

# The series binary: bin/zc.c compiled by PERFCC at PERFOPT.
$(PERFBIN): bin/zc.c $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o
	@$(PERFCC) -std=c17 -w $(PERFOPT) $(STAMPDEF) -o $@ $(MIMALLOC_OBJ) $(BUILDDIR)/buildstamp.o bin/zc.c $(call ZLINKOF,bin/zc.c,,,$(ZC)) -lpthread -lm

perf: $(PERFBIN)
	@echo "== zerolang line count (.z) =="
	@lsrc=$$(cat src/*.z $(SRC_SUBUNITS) | wc -l); llib=$$(cat lib/system/*.z lib/system/system/*.z lib/system/math/*.z | wc -l); \
	  printf "  src/**/*.z: %s    lib/system/**/*.z: %s    total: %s\n" "$$lsrc" "$$llib" "$$((lsrc + llib))"
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
ALLOC_BASELINE := 2816330
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
	@$(ELIDECC) -std=c17 -w $(PERFOPT) -o $(BUILDDIR)/zc-elide bin/zc.c $(call ZLINKOF,bin/zc.c,,,$(ZC)) -lpthread -lm
	@$(ELIDECC) -std=c17 -w $(PERFOPT) $(NOBUILTIN) -o $(BUILDDIR)/zc-noelide bin/zc.c $(call ZLINKOF,bin/zc.c,,,$(ZC)) -lpthread -lm
	@blocks() { valgrind --tool=memcheck $$1 $(PERFARGS) 2>&1 \
	    | grep 'total heap usage' | sed 's/.*usage: //;s/ allocs.*//;s/,//g'; }; \
	  kept=$$(blocks $(BUILDDIR)/zc-noelide); left=$$(blocks $(BUILDDIR)/zc-elide); \
	  test -n "$$kept" -a -n "$$left" || { echo "perf-elision: no allocation total -- a run failed"; exit 1; }; \
	  printf "  as emitted:            %s allocs\n" "$$kept"; \
	  printf "  after LLVM deletion:   %s allocs\n" "$$left"; \
	  printf "  write-only pool:       %s\n" "$$((kept - left))"

# sqlite-decls-guard -- the part of SQLite's C interface the sqlite unit's
# fragments declare instead of including (its @decls row) compiles after the
# vendored sqlite3.h without a diagnostic: a prototype, typedef or constant that
# drifts from the library fails here, rather than as a wrong call at run time.
sqlite-decls-guard: bin/zc
	@{ cat vendor/sqlite/sqlite3.h; bin/zc natives show sqlite.@decls --c --system lib/system; } \
	  | $(CC) -std=c17 -fsyntax-only -Wall -Werror -x c - \
	  || { echo "sqlite-decls-guard FAIL: sqlite's @decls row (lib/system/sqlite/nativec.zd) disagrees with vendor/sqlite/sqlite3.h -- match the declaration to the vendored header"; exit 1; }; \
	echo "sqlite-decls-guard OK: the sqlite fragments' declarations match the vendored sqlite3.h"

# shim-guard -- the C thread shim (system's prelude.@zthread row, written to
# $(NATIVEC_INC)/zthread_shim.inc) passes its own test under every compiler zc
# drives and under ThreadSanitizer: tests/c/zthread_shim_test.c exercises each
# primitive under contention, and the runner's ctest kind builds it and judges
# its exit status and output. TSan is proved first by the probes kind on a
# probe that races on purpose: a sanitizer that reports nothing there would
# make a clean shim run mean nothing. Where mingw-w64 and wine are installed the
# Win32 backend is built and run too (in a wine prefix kept under out/, which
# takes seconds to create once). On a Windows host gcc, clang and tcc build the
# Win32 backend natively, and TSan, which no Windows toolchain has, is skipped.
# MSVC is test-msvc-wine's, and ci-windows's.
SHIM_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --root . --jobs $(NPROC) --only ctest
SHIM_INC := -I$(NATIVEC_INC)
shim-guard: $(BUILDDIR)/ztestrunner $(BUILDDIR)/tcc $(BUILDDIR)/wineprefix/.ready $(NATIVEC_INC)/zthread_shim.inc
	@l=$(BUILDDIR)/shim-guard.log; : > $$l; \
	for cc in $(CC) clang; do \
	  $(SHIM_RUN) --cc $$cc --ccflags "$(SHIM_INC) $(THREADLIB)" >> $$l 2>&1 \
	    || { cat $$l; echo "shim-guard FAIL: under $$cc (above)"; exit 1; }; \
	done; \
	$(SHIM_RUN) --cc $(BUILDDIR)/tcc --ccflags "-B $(TCCLIB) -I $(TCCLIB)/include $(SHIM_INC) $(THREADLIB)" >> $$l 2>&1 \
	  || { cat $$l; echo "shim-guard FAIL: under tcc (above)"; exit 1; }; \
	tsan=" -- the Win32 backend, natively; ThreadSanitizer skipped (Linux only: no Windows toolchain has it)"; \
	if [ -z "$(WINDOWS_HOST)" ]; then \
	  { $(TSAN_RUN) --cc $(CC) --only probes \
	    && $(SHIM_RUN) --cc $(CC) --ccflags "-g -O1 -fsanitize=thread $(SHIM_INC) -lpthread"; } >> $$l 2>&1 \
	    || { cat $$l; echo "shim-guard FAIL: under ThreadSanitizer (above)"; exit 1; }; \
	  tsan=" and ThreadSanitizer (proved on a racy probe)"; \
	fi; \
	win="; Win32 not run (needs $(MINGW_CC) 14 or newer, for -mcrtdll=ucrt, and wine: sudo apt install gcc-mingw-w64-x86-64 wine)"; \
	[ -n "$(WINDOWS_HOST)" ] && win=""; \
	if [ -z "$(WINDOWS_HOST)" ] && $(MINGW_UCRT_CHECK) && command -v wine >/dev/null 2>&1; then \
	  $(WINE_SERVER_UP); \
	  $(WINE_ENV) $(SHIM_RUN) --cc $(MINGW_CC) --ccflags "$(MINGW_CFLAGS) $(SHIM_INC)" --exec wine --timeout 300 >> $$l 2>&1 \
	    || { cat $$l; echo "shim-guard FAIL: the Win32 backend (mingw-w64, under wine; above)"; exit 1; }; \
	  win=", and the Win32 backend (mingw-w64 under wine)"; \
	fi; \
	echo "shim-guard OK: the thread shim's test passes under $(CC), clang, tcc$$tsan$$win"

# thread-tsan-guard -- every threaded run fixture (thread_*, sync_*) under
# ThreadSanitizer, built by $(CC) and by clang: the stdout and exit status its
# goldens hold, which a race fails (ThreadSanitizer exits 66). TSan is proved
# first by the runner's probes kind on tests/c/tsan_probe.c, which races on
# purpose, so a sanitizer that reports nothing cannot pass the rest. A race
# shows only in some schedules, so each program then runs TSAN_REPEAT more
# times (--repeat), $(NPROC) at once, and every run must hold.
TSAN_REPEAT ?= 20
TSAN_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --root . --jobs $(NPROC)
ifdef WINDOWS_HOST
thread-tsan-guard:
	@echo "thread-tsan-guard: skipped (Linux only: ThreadSanitizer, which no Windows toolchain has)"
else
thread-tsan-guard: bin/zc $(BUILDDIR)/ztestrunner
	@: > $(BUILDDIR)/thread-tsan.log; \
	for cc in $(CC) clang; do \
	  { $(TSAN_RUN) --cc $$cc --only probes \
	    && $(TSAN_RUN) --cc $$cc --only run --cases thread_ --cases sync_ \
	         --ccflags "-g -O1 -fsanitize=thread" --repeat $(TSAN_REPEAT); } >> $(BUILDDIR)/thread-tsan.log 2>&1 \
	    || { cat $(BUILDDIR)/thread-tsan.log; echo "thread-tsan-guard FAIL: under $$cc (above): a race, or a threaded program that differs from its goldens"; exit 1; }; \
	done; \
	echo "thread-tsan-guard OK: the threaded fixtures race-free under ThreadSanitizer ($(CC), clang), each run $$(($(TSAN_REPEAT) + 1)) times, proved on a racy probe"
endif

# mode-parity -- --cc-mode inproc builds the same PROGRAM as spawn: the run
# kind has zc build every run case with tcc itself (--zc-build), once in each
# mode, and judges each program against its goldens, never the bytes. The
# programs zc refuses for tcc, $(TCC_KNOWN)'s, must be refused in both modes,
# and every other one must print its golden and exit as it says.
MODE_RUN = $(BUILDDIR)/ztestrunner --zc bin/zc --root . --jobs $(NPROC) --only run --zc-build --cc tcc --known $(TCC_KNOWN)
mode-parity: bin/zc $(BUILDDIR)/tcc $(BUILDDIR)/ztestrunner
	@for m in spawn inproc; do \
	  $(MODE_RUN) --zc-flag --cc-mode --zc-flag $$m > $(BUILDDIR)/mode-parity.log 2>&1 \
	    || { cat $(BUILDDIR)/mode-parity.log; echo "mode-parity FAIL: zc build --cc tcc --cc-mode $$m (above): a program that differs from its golden, or a refusal $(TCC_KNOWN) does not list"; exit 1; }; \
	done; \
	echo "mode-parity OK: zc build --cc tcc builds every run case to its goldens under --cc-mode spawn and inproc, refusing $(TCC_KNOWN)'s in both"

# view-guard -- a native receiver marked `.view` is const in the C function that
# takes it, and a const one is marked `.view`, so the C compiler proves a viewed
# receiver is never written (`zc natives check views`, src/znativeview.z). The
# register tests/view_guard.zd places the receivers no C function the check
# reads takes, and each of its entries must still place one.
view-guard: bin/zc
	@bin/zc natives check views --system lib/system --emitter src/zemitterc.z --register tests/view_guard.zd

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
