static char* z_sv_to_cstr(z_@StringView@_t v) {
    char* buf = (char*)z_xmalloc(v.size + 1);
    if (v.size > 0) memcpy(buf, v.data, v.size);
    buf[v.size] = '\0';
    return buf;
}

