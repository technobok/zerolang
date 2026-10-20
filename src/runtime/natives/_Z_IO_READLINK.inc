/* readlink — follow-free read of a symlink's target. readlink(2)
   does not NUL-terminate, and we don't know the exact target size
   up front; loop with a growing buffer until the kernel says the
   full target fit. EINVAL from readlink means the path exists but
   isn't a symbolic link -- surfaced as invalidpath with a
   caller-readable message. On Windows the target is the reparse
   point's substitute name as stored (z_io_win_readlink), a junction's
   included. The success path breaks out so the body ends
   in its return -- c2mir miscompiles a struct return otherwise. */
#if defined(_WIN32)
/* a symbolic link's or junction's reparse data, as FSCTL_GET_REPARSE_POINT
   returns it (the SDK keeps the type in the driver kit) */
typedef struct {
    ULONG tag;
    USHORT data_length;
    USHORT reserved;
    USHORT sub_offset;
    USHORT sub_length;
    USHORT print_offset;
    USHORT print_length;
    /* a symbolic link has a Flags word here; a junction's path follows */
    ULONG flags;
} z_io_win_reparse;

/* the stored target of the link at `path`, as UTF-8 (`\??\` stripped, a
   share's `UNC\` given back its `\\`); NULL with *err the Win32 code,
   ERROR_NOT_A_REPARSE_POINT for anything but a symbolic link or junction */
static char* z_io_win_readlink(z_@StringView@_t path, size_t* len, int* err) {
    HANDLE h = z_win_open(
        path.data, path.size, 0, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS
    );
    if (h == INVALID_HANDLE_VALUE) {
        *err = (int)GetLastError();
        return NULL;
    }
    union {
        z_io_win_reparse r;
        unsigned char raw[MAXIMUM_REPARSE_DATA_BUFFER_SIZE];
    } buf;
    DWORD got = 0;
    BOOL ok = DeviceIoControl(h, FSCTL_GET_REPARSE_POINT, NULL, 0, &buf, sizeof buf, &got, NULL);
    DWORD e = GetLastError();
    CloseHandle(h);
    if (!ok) {
        *err = (int)e;
        return NULL;
    }
    const unsigned char* names;
    if (buf.r.tag == IO_REPARSE_TAG_SYMLINK) {
        names = buf.raw + sizeof(z_io_win_reparse);
    } else if (buf.r.tag == IO_REPARSE_TAG_MOUNT_POINT) {
        names = buf.raw + offsetof(z_io_win_reparse, flags);
    } else {
        *err = ERROR_NOT_A_REPARSE_POINT;
        return NULL;
    }
    const wchar_t* sub = (const wchar_t*)(names + buf.r.sub_offset);
    int n = (int)(buf.r.sub_length / sizeof(wchar_t));
    if (n >= 4 && sub[0] == L'\\' && sub[1] == L'?' && sub[2] == L'?' && sub[3] == L'\\') {
        sub += 4;
        n -= 4;
    }
    int unc = n >= 4 && sub[0] == L'U' && sub[1] == L'N' && sub[2] == L'C' && sub[3] == L'\\';
    if (unc) {
        sub += 3;
        n -= 3;
    }
    size_t un = 0;
    char* u = z_win_utf8(sub, n, &un);
    if (u == NULL) {
        *err = (int)GetLastError();
        return NULL;
    }
    if (unc) {
        char* v = (char*)z_xmalloc(un + 2);
        v[0] = '\\';
        memcpy(v + 1, u, un + 1);
        free(u);
        u = v;
        un++;
    }
    *len = un;
    return u;
}
#endif

static z_@Result_String_IoError@_t z_io_readlink(z_@StringView@_t path);
static z_@Result_String_IoError@_t z_io_readlink(z_@StringView@_t path) {
    z_@Result_String_IoError@_t result = {0};
#if defined(_WIN32)
    size_t tn = 0;
    int e = 0;
    char* t = z_io_win_readlink(path, &tn, &e);
    if (t == NULL) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        if (e == ERROR_NOT_A_REPARSE_POINT) {
            z_@IoError@_t v = {0};
            v.tag = @ARM_IoError.invalidpath@;
            z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
            *sp = z_@String@_new("not a symbolic link");
            v.data = sp;
            *boxed = v;
        } else {
            *boxed = z_io_os_error(e);
        }
        result.tag = @ARM_Result_String_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@String@_t* tb = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *tb = z_@String@_new(t);
    free(t);
    result.tag = @ARM_Result_String_IoError.ok@;
    result.data = tb;
    return result;
#else
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
                v.tag = @ARM_IoError.invalidpath@;
                z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
                *sp = z_@String@_new("not a symbolic link");
                v.data = sp;
                *boxed = v;
            } else {
                *boxed = z_io_os_error(e);
            }
            result.tag = @ARM_Result_String_IoError.err@;
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
    result.tag = @ARM_Result_String_IoError.ok@;
    result.data = boxed;
    return result;
#endif
}


