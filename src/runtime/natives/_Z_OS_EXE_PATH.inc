/* os.exePath -- absolute path of the running executable. Linux
   reads /proc/self/exe; macOS uses _NSGetExecutablePath. Copied
   into a z_@String@_t so ownership stays uniform with the other os
   string natives. */
#if defined(__APPLE__)
#include <mach-o/dyld.h>
#endif
static z_@Result_String_IoError@_t z_os_exePath(void);
static z_@Result_String_IoError@_t z_os_exePath(void) {
    z_@Result_String_IoError@_t out = {0};
    char buf[4096];
#if defined(__APPLE__)
    uint32_t bufsize = (uint32_t)sizeof(buf);
    if (_NSGetExecutablePath(buf, &bufsize) != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        z_@IoError@_t e = {0};
        e.tag = @ARM_IoError.other@;
        *boxed = e;
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
#else
    ssize_t len = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (len < 0) {
        int e = errno;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        out.tag = @ARM_Result_String_IoError.err@;
        out.data = boxed;
        return out;
    }
    buf[len] = '\0';
#endif
    z_@String@_t* boxed = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *boxed = z_@String@_new(buf);
    out.tag = @ARM_Result_String_IoError.ok@;
    out.data = boxed;
    return out;
}


