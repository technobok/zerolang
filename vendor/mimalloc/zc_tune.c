/* Allocator tuning hook for the zerolang driver binaries. Currently
   default-neutral: mimalloc's stock options apply (self-compile measured
   0.80s / 121MB peak vs glibc 0.93s / 113.5MB). Uncommenting the
   purge tuning trades ~1% wall for ~5MB RSS (0.81s / 116MB); the same
   knob is available at runtime as MIMALLOC_PURGE_DELAY=0. */
#include "mimalloc.h"

__attribute__((constructor)) static void zc_mimalloc_tune(void) {
    /* mi_option_set(mi_option_purge_delay, 0); */
}
