/* readlink — follow-free read of a symlink's target. readlink(2)
   does not NUL-terminate, and we don't know the exact target size
   up front; loop with a growing buffer until the kernel says the
   full target fit. EINVAL from readlink means the path exists but
   isn't a symbolic link -- surfaced as invalidpath with a
   caller-readable message. The success path breaks out so the body ends
   in its return -- c2mir miscompiles a struct return otherwise. */
static z_@Result_String_IoError@_t z_io_readlink(z_@StringView@_t path);
static z_@Result_String_IoError@_t z_io_readlink(z_@StringView@_t path) {
    z_@Result_String_IoError@_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    uint64_t cap = (uint64_t)256;
    char* buf = (char*)z_xmalloc(cap);
    ssize_t n;
    for (;;) {
        n = readlink(path_cstr, buf, cap);
        if (n < 0) {
            int e = errno;
            free(buf);
            free(path_cstr);
            z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
            if (e == EINVAL) {
                z_@IoError@_t v = {0};
                v.tag = Z_IOERROR_TAG_INVALIDPATH;
                z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
                *sp = z_@String@_new("not a symbolic link");
                v.data = sp;
                *boxed = v;
            } else {
                *boxed = z_io_errno_to_IoError(e);
            }
            result.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
            result.data = boxed;
            return result;
        }
        if ((uint64_t)n < cap) break;
        cap *= 2;
        buf = (char*)z_xrealloc(buf, cap);
    }
    z_@String@_t target = {0};
    target.size = (uint64_t)n;
    target.capacity = target.size + 1;
    target.data = (char*)z_xmalloc(target.capacity + 1);
    memcpy(target.data, buf, target.size);
    target.data[target.size] = '\0';
    free(buf);
    free(path_cstr);
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = target;
    result.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

