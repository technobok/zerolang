/* Heap-allocate a NUL-terminated copy of the view. Free with free(). */
static char* z_sv_to_cstr(z_StringView_t v) {
    char* buf = (char*)z_xmalloc(v.length + 1);
    if (v.length > 0) memcpy(buf, v.data, v.length);
    buf[v.length] = '\0';
    return buf;
}

