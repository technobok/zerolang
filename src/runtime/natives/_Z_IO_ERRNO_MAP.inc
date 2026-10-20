/* an OS error code -> IoError: errno on POSIX, a Win32 error code (GetLastError,
   WSAGetLastError) on Windows. A code no arm names becomes `other`, carrying
   "<the OS's message> (os error N)" so nothing is lost in translation; so does
   a malformed path, under `invalidpath`. POSIX's message comes from the
   reentrant strerror_r -- the XSI form, as the prelude asks for POSIX without
   _GNU_SOURCE -- and Windows' from FormatMessageW. */
static z_@String@_t* z_io_os_error_text(int code) {
    char text[640];
#if defined(_WIN32)
    char* msg = z_win_error_text((DWORD)code);
    snprintf(text, sizeof text, "%s (os error %d)", msg ? msg : "unknown error", code);
    free(msg);
#else
    char msg[256];
    int failed = strerror_r(code, msg, sizeof msg);
    snprintf(text, sizeof text, "%s (os error %d)", failed ? "unknown error" : msg, code);
#endif
    z_@String@_t* sp = (z_@String@_t*)z_xmalloc(sizeof(z_@String@_t));
    *sp = z_@String@_new(text);
    return sp;
}

static z_@IoError@_t z_io_os_error(int code) {
    z_@IoError@_t r = {0};
    r.data = NULL;
    switch (code) {
#if defined(_WIN32)
        case ERROR_FILE_NOT_FOUND:
        case ERROR_PATH_NOT_FOUND:
        case ERROR_INVALID_DRIVE:
        case ERROR_BAD_NETPATH:
        case ERROR_BAD_NET_NAME:          r.tag = @ARM_IoError.notfound@; break;
        case ERROR_ACCESS_DENIED:
        case ERROR_SHARING_VIOLATION:
        case ERROR_LOCK_VIOLATION:
        case ERROR_PRIVILEGE_NOT_HELD:    r.tag = @ARM_IoError.permissiondenied@; break;
        case WSAEINTR:                    r.tag = @ARM_IoError.interrupted@; break;
        case ERROR_ALREADY_EXISTS:
        case ERROR_FILE_EXISTS:           r.tag = @ARM_IoError.exists@; break;
        case ERROR_DIRECTORY_NOT_SUPPORTED: r.tag = @ARM_IoError.isdir@; break;
        case ERROR_DIRECTORY:             r.tag = @ARM_IoError.notdir@; break;
        case ERROR_DISK_FULL:
        case ERROR_HANDLE_DISK_FULL:      r.tag = @ARM_IoError.nospace@; break;
        case ERROR_INVALID_NAME:
        case ERROR_BAD_PATHNAME:
        case ERROR_FILENAME_EXCED_RANGE:
        case ERROR_NO_UNICODE_TRANSLATION:
            r.tag = @ARM_IoError.invalidpath@;
            r.data = z_io_os_error_text(code);
            break;
#else
        case ENOENT:  r.tag = @ARM_IoError.notfound@; break;
        case EACCES:
        case EPERM:   r.tag = @ARM_IoError.permissiondenied@; break;
        case EINTR:   r.tag = @ARM_IoError.interrupted@; break;
        case EEXIST:  r.tag = @ARM_IoError.exists@; break;
        case EISDIR:  r.tag = @ARM_IoError.isdir@; break;
        case ENOTDIR: r.tag = @ARM_IoError.notdir@; break;
        case ENOSPC:  r.tag = @ARM_IoError.nospace@; break;
#endif
        default:
            r.tag = @ARM_IoError.other@;
            r.data = z_io_os_error_text(code);
            break;
    }
    return r;
}
