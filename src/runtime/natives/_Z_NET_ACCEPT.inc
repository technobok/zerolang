/* listener.accept — block for one connection; EINTR retries. accept4 sets
   close-on-exec atomically, so a child spawned meanwhile never inherits the
   connection; it is reached through syscall() because its libc declaration
   needs _GNU_SOURCE, which the prelude does not define. */
static z_@Result_Conn_IoError@_t z_@Listener@_accept(z_@Listener@_t* l) {
    z_@Result_Conn_IoError@_t result = {0};
    int fd;
    for (;;) {
        fd = (int)syscall(SYS_accept4, l->@MEMBER_Listener.fd@, NULL, NULL, SOCK_CLOEXEC);
        if (fd >= 0) break;
        if (errno == EINTR) continue;
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(errno);
        result.tag = @ARM_Result_Conn_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@Conn@_t* boxed = (z_@Conn@_t*)z_xmalloc(sizeof(z_@Conn@_t));
    boxed->@MEMBER_Conn.fd@ = (int32_t)fd;
    boxed->@MEMBER_Conn.open@ = true;
    result.tag = @ARM_Result_Conn_IoError.ok@;
    result.data = boxed;
    return result;
}

