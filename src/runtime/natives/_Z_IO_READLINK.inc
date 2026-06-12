/* readlink — follow-free read of a symlink's target. readlink(2)
   does not NUL-terminate, and we don't know the exact target size
   up front; loop with a growing buffer until the kernel says the
   full target fit. EINVAL from readlink means the path exists but
   isn't a symbolic link -- surfaced as invalidpath with a
   caller-readable message. */
static z_Result_String_IoError_t z_io_readlink(z_StringView_t path);
static z_Result_String_IoError_t z_io_readlink(z_StringView_t path) {
    z_Result_String_IoError_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    uint64_t cap = (uint64_t)256;
    char* buf = (char*)z_xmalloc(cap);
    for (;;) {
        ssize_t n = readlink(path_cstr, buf, cap);
        if (n < 0) {
            int e = errno;
            free(buf);
            free(path_cstr);
            z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
            if (e == EINVAL) {
                z_IoError_t v = {0};
                v.tag = Z_IOERROR_TAG_INVALIDPATH;
                z_String_t* sp = (z_String_t*)z_xmalloc(sizeof(z_String_t));
                *sp = z_String_new("not a symbolic link");
                v.data = sp;
                *boxed = v;
            } else {
                *boxed = z_io_errno_to_IoError(e);
            }
            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
        if ((uint64_t)n < cap) {
            z_String_t target = {0};
            target.size = (uint64_t)n;
            target.capacity = target.size + 1;
            target.data = (char*)z_xmalloc(target.capacity + 1);
            memcpy(target.data, buf, target.size);
            target.data[target.size] = '\0';
            free(buf);
            free(path_cstr);
            z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
            *boxed = target;
            result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
            result.data = boxed;
            return result;
        }
        cap *= 2;
        buf = (char*)z_xrealloc(buf, cap);
    }
}

