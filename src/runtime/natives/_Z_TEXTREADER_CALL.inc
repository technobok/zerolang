/* textreader.call -- iterator hook. Delegates to read_line and
   collapses the result(string, ioerror) into option(string):
   ok(s) -> some(s), any err -> none. The eof / badencoding / I/O
   arms all terminate iteration silently; callers who need to
   distinguish them must use read_line directly. */
static z_Option_String_t z_TextReader_call(z_TextReader_t* self);
static z_Option_String_t z_TextReader_call(z_TextReader_t* self) {
    z_Option_String_t out = {0};
    z_Result_String_IoError_t rr = z_TextReader_readLine(self);
    if (rr.tag == Z_RESULT_STRING_IOERROR_TAG_OK) {
        /* Move the heap-owned string out of rr's ok box into a
           fresh some box; free the source box without calling
           the string destructor (ownership moved). */
        z_String_t* src = (z_String_t*)rr.data;
        z_String_t* dst = (z_String_t*)z_xmalloc(sizeof(z_String_t));
        *dst = *src;
        free(src);
        out.tag = Z_OPTION_STRING_TAG_SOME;
        out.data = dst;
        return out;
    }
    /* err arm: run the ioerror's destructor + free the box so
       string payloads (invalidpath / other) don't leak. */
    if (rr.data) {
        z_IoError_destroy((z_IoError_t*)rr.data);
        free(rr.data);
    }
    out.tag = Z_OPTION_STRING_TAG_NONE;
    return out;
}

