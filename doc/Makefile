PICODOC  = ../../picodoc-c/picodoc
SRCS    := $(wildcard *.pdoc)
HTML    := $(SRCS:.pdoc=.html)

all: $(HTML)

%.html: %.pdoc $(PICODOC)
	$(PICODOC) $< -o $@

clean:
	rm -f $(HTML)

.PHONY: all clean
