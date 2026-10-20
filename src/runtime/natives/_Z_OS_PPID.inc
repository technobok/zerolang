/* os.ppid -- getppid. Windows keeps no parent link a process can ask
   for directly, so it is this process's entry in a Toolhelp snapshot;
   0 when the snapshot cannot be taken. */
#if defined(_WIN32)
#include <tlhelp32.h>
#endif
static int32_t z_os_ppid(void);
static int32_t z_os_ppid(void) {
#if defined(_WIN32)
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    DWORD me = GetCurrentProcessId();
    DWORD parent = 0;
    PROCESSENTRY32W pe;
    pe.dwSize = sizeof pe;
    for (BOOL more = Process32FirstW(snap, &pe); more; more = Process32NextW(snap, &pe)) {
        if (pe.th32ProcessID == me) {
            parent = pe.th32ParentProcessID;
            break;
        }
    }
    CloseHandle(snap);
    return (int32_t)parent;
#else
    return (int32_t)getppid();
#endif
}
