/* tsan_probe.c -- a data race on purpose: two threads increment one plain int.
   `make shim-guard` requires ThreadSanitizer to REPORT it before trusting a
   clean TSan run of the shim test, so a sanitizer that silently does nothing
   cannot pass the guard. */
#define _POSIX_C_SOURCE 200809L
#include <pthread.h>
#include <stdio.h>

static int shared;

static void* bump(void* arg) {
    (void)arg;
    for (int i = 0; i < 100000; i++) shared++;
    return NULL;
}

int main(void) {
    pthread_t a, b;
    pthread_create(&a, NULL, bump, NULL);
    pthread_create(&b, NULL, bump, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    printf("%d\n", shared);
    return 0;
}
