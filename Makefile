ZC       := uv run python src/zc.py
CC       := gcc
CFLAGS   := -Wall -Wno-unused-function
BUILDDIR := out

# all .z files in examples/
EXAMPLES := $(wildcard examples/*.z)
NAMES    := $(basename $(notdir $(EXAMPLES)))

.PHONY: check test fmt build clean

check:
	uv run ruff format src/ tests/
	uv run ruff check src/ tests/ --fix
	uv run ty check src/

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
