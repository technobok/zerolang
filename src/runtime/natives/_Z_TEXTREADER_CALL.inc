/* textreader.call -- iterator hook. Delegates to read_line and
   collapses the result(string, ioerror) into option(string):
   ok(s) -> some(s), any err -> none. The eof / badencoding / I/O
   arms all terminate iteration silently; callers who need to
   distinguish them must use read_line directly. */
static z_@Option_String@_t z_@TextReader@_call(z_@TextReader@_t* self);
static z_@Option_String@_t z_@TextReader@_call(z_@TextReader@_t* self) {
    z_@Option_String@_t out = {0};
    z_@Result_String_IoError@_t rr = z_@TextReader@_readLine(self);
    if (rr.tag == @ARM_Result_String_IoError.ok@) {
        /* Move the heap-owned string out of rr's ok box into a
           fresh some box; free the source box without calling
           the string destructor (ownership moved). */
        z_@String@_t* src = (z_@String@_t*)rr.data;
        z_@String@_t* dst = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
        *dst = *src;
        free(src);
        out.tag = @ARM_Option_String.some@;
        out.data = dst;
        return out;
    }
    /* err arm: run the ioerror's destructor + free the box so
       string payloads (invalidpath / other) don't leak. */
    if (rr.data) {
        z_@IoError@_destroy((z_@IoError@_t*)rr.data);
        free(rr.data);
    }
    out.tag = @ARM_Option_String.none@;
    return out;
}


