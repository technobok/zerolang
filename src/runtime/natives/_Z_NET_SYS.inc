/* z_net -- the socket layer under net's natives: POSIX sockets, or Winsock,
   started once on first use (net's natives.tbl unit row links ws2_32). A socket is
   net's `socket`, an int64. Every call answers -1 on failure, the reason
   left for z_net_error -- errno, or WSAGetLastError. A socket is never
   inherited by a child, and a write to a peer that has gone fails with EPIPE
   rather than raising SIGPIPE. */
#if defined(_WIN32)
#include <winsock2.h>
#ifndef WSA_FLAG_NO_HANDLE_INHERIT
#define WSA_FLAG_NO_HANDLE_INHERIT 0x80
#endif
static INIT_ONCE z_net_once = INIT_ONCE_STATIC_INIT;

static BOOL CALLBACK z_net_start(PINIT_ONCE once, PVOID arg, PVOID* ctx) {
    (void)once;
    (void)arg;
    (void)ctx;
    WSADATA d;
    return WSAStartup(MAKEWORD(2, 2), &d) == 0;
}

/* the socket as the Winsock calls take it */
#define Z_NET_RAW(s) ((SOCKET)(s))

static int z_net_error(void) {
    return WSAGetLastError();
}

static int64_t z_net_socket(void) {
    if (!InitOnceExecuteOnce(&z_net_once, z_net_start, NULL, NULL)) {
        WSASetLastError(WSANOTINITIALISED);
        return -1;
    }
    SOCKET s = WSASocketW(AF_INET, SOCK_STREAM, IPPROTO_TCP, NULL, 0, WSA_FLAG_NO_HANDLE_INHERIT);
    return s == INVALID_SOCKET ? -1 : (int64_t)s;
}

static int64_t z_net_accept(int64_t l) {
    SOCKET s = accept((SOCKET)l, NULL, NULL);
    if (s == INVALID_SOCKET) return -1;
    SetHandleInformation((HANDLE)s, HANDLE_FLAG_INHERIT, 0);
    return (int64_t)s;
}

static int z_net_close(int64_t s) {
    return closesocket((SOCKET)s) == 0 ? 0 : -1;
}

static int64_t z_net_recv(int64_t s, void* b, uint64_t n) {
    return recv((SOCKET)s, (char*)b, n > INT_MAX ? INT_MAX : (int)n, 0);
}

static int64_t z_net_send(int64_t s, const void* b, uint64_t n) {
    return send((SOCKET)s, (const char*)b, n > INT_MAX ? INT_MAX : (int)n, 0);
}

/* a port another socket holds is refused rather than shared */
static int z_net_exclusive(int64_t s) {
    BOOL one = TRUE;
    return setsockopt((SOCKET)s, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&one, sizeof one) == 0 ? 0 : -1;
}

/* 1 when the socket is readable (or the peer closed) within ms, 0 when not,
   -1 on failure */
static int z_net_poll(int64_t s, int ms) {
    WSAPOLLFD p;
    p.fd = (SOCKET)s;
    p.events = POLLRDNORM;
    p.revents = 0;
    int r = WSAPoll(&p, 1, ms);
    if (r < 0) return WSAGetLastError() == WSAEINTR ? 0 : -1;
    return (r > 0 && (p.revents & (POLLRDNORM | POLLHUP | POLLERR))) ? 1 : 0;
}

/* the port a bound socket holds, or -1 */
static int32_t z_net_port(int64_t s) {
    struct sockaddr_in a;
    int n = sizeof a;
    if (getsockname((SOCKET)s, (struct sockaddr*)&a, &n) != 0) return -1;
    return ntohs(a.sin_port);
}

static int z_net_interrupted(int e) {
    return e == WSAEINTR;
}
#else
/* the socket as the POSIX calls take it */
#define Z_NET_RAW(s) ((int)(s))

static int z_net_error(void) {
    return errno;
}

static int64_t z_net_socket(void) {
    return socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
}

/* accept4 sets close-on-exec atomically; it is reached through syscall()
   because its libc declaration needs _GNU_SOURCE, which the prelude does not
   define */
static int64_t z_net_accept(int64_t l) {
    return (int64_t)syscall(SYS_accept4, (int)l, NULL, NULL, SOCK_CLOEXEC);
}

static int z_net_close(int64_t s) {
    return close((int)s);
}

static int64_t z_net_recv(int64_t s, void* b, uint64_t n) {
    return recv((int)s, b, n, 0);
}

static int64_t z_net_send(int64_t s, const void* b, uint64_t n) {
#ifdef MSG_NOSIGNAL
    return send((int)s, b, n, MSG_NOSIGNAL);
#else
    return send((int)s, b, n, 0);
#endif
}

/* SO_REUSEADDR only lets a port in TIME_WAIT be bound again: one a socket
   holds is still refused */
static int z_net_exclusive(int64_t s) {
    int one = 1;
    return setsockopt((int)s, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
}

static int z_net_poll(int64_t s, int ms) {
    struct pollfd p = { (int)s, POLLIN, 0 };
    int r = poll(&p, 1, ms);
    if (r < 0) return errno == EINTR ? 0 : -1;
    return (r > 0 && (p.revents & (POLLIN | POLLHUP))) ? 1 : 0;
}

static int32_t z_net_port(int64_t s) {
    struct sockaddr_in a;
    socklen_t n = sizeof a;
    if (getsockname((int)s, (struct sockaddr*)&a, &n) != 0) return -1;
    return ntohs(a.sin_port);
}

static int z_net_interrupted(int e) {
    return e == EINTR;
}
#endif

/* the IoError an OS error code makes, boxed for a Result's err arm */
static z_@IoError@_t* z_net_boxed_error(int e) {
    z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
    *boxed = z_io_os_error(e);
    return boxed;
}
