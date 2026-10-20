/* file.pollReadable — whether a read would find data, or the end to drain,
   within timeoutMillis. POSIX polls the fd (POLLIN|POLLHUP); an interrupted
   wait reports not-readable. Windows asks the handle by its kind: a disk
   file is always ready; a pipe answers PeekNamedPipe (bytes waiting, or its
   writer gone), asked every millisecond or so until the time is up; a
   console is waited on (WaitForSingleObject). */
static z_@ResultVR_bool_IoError@_t z_@File@_pollReadable(const z_@File@_t* f, int32_t timeoutMillis);
static z_@ResultVR_bool_IoError@_t z_@File@_pollReadable(const z_@File@_t* f, int32_t timeoutMillis) {
    z_@ResultVR_bool_IoError@_t result = {0};
    int ready = 0;
    int e = 0;
#if defined(_WIN32)
    HANDLE h = (HANDLE)_get_osfhandle(f->@MEMBER_File.fd@);
    DWORD kind = GetFileType(h);
    if (kind == FILE_TYPE_PIPE) {
        ULONGLONG start = GetTickCount64();
        for (;;) {
            DWORD avail = 0;
            if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL)) {
                DWORD pe = GetLastError();
                if (pe == ERROR_BROKEN_PIPE) ready = 1; else e = (int)pe;
                break;
            }
            if (avail > 0) {
                ready = 1;
                break;
            }
            if (timeoutMillis >= 0 && GetTickCount64() - start >= (ULONGLONG)timeoutMillis) break;
            Sleep(1);
        }
    } else if (kind == FILE_TYPE_CHAR) {
        DWORD w = WaitForSingleObject(h, timeoutMillis < 0 ? INFINITE : (DWORD)timeoutMillis);
        if (w == WAIT_OBJECT_0) ready = 1;
        else if (w == WAIT_FAILED) e = (int)GetLastError();
    } else {
        ready = 1;
    }
#else
    struct pollfd p = { f->@MEMBER_File.fd@, POLLIN, 0 };
    int rc = poll(&p, 1, timeoutMillis);
    if (rc < 0 && errno != EINTR) e = errno;
    ready = rc > 0 && (p.revents & (POLLIN | POLLHUP));
#endif
    if (e != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_ResultVR_bool_IoError.err@;
        result.data = boxed;
        return result;
    }
    result.tag = @ARM_ResultVR_bool_IoError.ok@;
    result.@MEMBER_ResultVR_bool_IoError.ok@ = ready != 0;
    return result;
}
