/* errno -> IoError. An errno no arm names becomes `other`, carrying
   "<the C library's message> (os error N)" so nothing is lost in translation.
   The message comes from the reentrant strerror_r -- the XSI form, as the
   prelude asks for POSIX without _GNU_SOURCE -- or strerror_s on Windows. */
static z_@IoError@_t z_io_errno_to_IoError(int e) {
    z_@IoError@_t r = {0};
    r.data = NULL;
    switch (e) {
        case ENOENT:  r.tag = @ARM_IoError.notfound@; break;
        case EACCES:
        case EPERM:   r.tag = @ARM_IoError.permissiondenied@; break;
        case EINTR:   r.tag = @ARM_IoError.interrupted@; break;
        case EEXIST:  r.tag = @ARM_IoError.exists@; break;
        case EISDIR:  r.tag = @ARM_IoError.isdir@; break;
        case ENOTDIR: r.tag = @ARM_IoError.notdir@; break;
        case ENOSPC:  r.tag = @ARM_IoError.nospace@; break;
        default: {
            char msg[256];
#if defined(_WIN32)
            int failed = strerror_s(msg, sizeof msg, e);
#else
            int failed = strerror_r(e, msg, sizeof msg);
#endif
            char text[320];
            snprintf(text, sizeof text, "%s (os error %d)", failed ? "unknown error" : msg, e);
            z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
            *sp = z_@String@_new(text);
            r.tag = @ARM_IoError.other@;
            r.data = sp;
            break;
        }
    }
    return r;
}
