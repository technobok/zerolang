/* os.exePath -- absolute path of the running executable. Linux
   reads /proc/self/exe; macOS uses _NSGetExecutablePath. Copied
   into a z_String_t so ownership stays uniform with the other os
   string natives. */
#if defined(__APPLE__)
#include <mach-o/dyld.h>
#endif
static z_Result_String_IoError_t z_os_exePath(void);
static z_Result_String_IoError_t z_os_exePath(void) {
    z_Result_String_IoError_t out = {0};
    char buf[4096];
#if defined(__APPLE__)
    uint32_t bufsize = (uint32_t)sizeof(buf);
    if (_NSGetExecutablePath(buf, &bufsize) != 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        z_IoError_t e = {0};
        e.tag = Z_IOERROR_TAG_OTHER;
        *boxed = e;
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
#else
    ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (len < 0) {
        int e = errno;
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = Z_RESULT_STRING_IOERROR_TAG_ERR;
        out.data = boxed;
        return out;
    }
    buf[len] = '\0';
#endif
    z_String_t* boxed = (z_String_t*)z_xmalloc(sizeof(z_String_t));
    *boxed = z_String_new(buf);
    out.tag = Z_RESULT_STRING_IOERROR_TAG_OK;
    out.data = boxed;
    return out;
}

