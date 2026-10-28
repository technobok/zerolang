/* z_start -- what C's `main` runs before `z_main`: the hash seed in a
   program that hashes, and on Windows the standard streams switched to binary
   mode, so a program's bytes reach them as written -- no "\n" -> "\r\n"
   translation -- as on every other host. */
static void z_start(void) {
#if defined(_WIN32)
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
#endif
/*@@row @prelude.seed_start demand=@seed@@*/
    z_siphash_init();
/*@@end@@*/
}
