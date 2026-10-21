/* zconsole_read.c -- what a program shows on a Windows console, checked where
   no console is: `zconsole_read EXPECTED PROGRAM [ARG...]` gives itself a new
   console, runs PROGRAM on it with the ARGs, reads the screen back as UTF-16
   (ReadConsoleOutputCharacterW), and compares its lines, trailing blanks
   dropped, with the UTF-8 file EXPECTED. Built and run by `make windows-guest`
   with UCRT64 gcc, where ssh gives every program pipes rather than a console.
   Exit status 0 and "zconsole_read OK" mean the screen held EXPECTED and the
   program exited 0; the report goes to the standard output it was started
   with. Windows only. */
#if defined(_WIN32)
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static HANDLE report;

static void say(const char* s) {
    DWORD put;
    WriteFile(report, s, (DWORD)strlen(s), &put, NULL);
}

static char* utf8_of(const wchar_t* w, int n) {
    int need = WideCharToMultiByte(CP_UTF8, 0, w, n, NULL, 0, NULL, NULL);
    char* s = (char*)malloc((size_t)need + 1);
    if (s == NULL) return NULL;
    WideCharToMultiByte(CP_UTF8, 0, w, n, s, need, NULL, NULL);
    s[need] = 0;
    return s;
}

/* the command line CreateProcessW takes: each argument quoted, a quote or a
   run of backslashes before one escaped as CommandLineToArgvW reads them */
static void quote_into(wchar_t* out, size_t* at, const wchar_t* a) {
    size_t k = *at;
    if (k > 0) out[k++] = L' ';
    out[k++] = L'"';
    for (const wchar_t* p = a;; p++) {
        size_t bs = 0;
        while (*p == L'\\') {
            bs++;
            p++;
        }
        size_t copies = (*p == 0) ? bs * 2 : (*p == L'"') ? bs * 2 + 1 : bs;
        for (size_t i = 0; i < copies; i++) out[k++] = L'\\';
        if (*p == 0) break;
        out[k++] = *p;
    }
    out[k++] = L'"';
    out[k] = 0;
    *at = k;
}

int main(void) {
    report = GetStdHandle(STD_OUTPUT_HANDLE);
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv == NULL || argc < 3) {
        say("usage: zconsole_read EXPECTED PROGRAM [ARG...]\n");
        return 2;
    }
    FILE* ef = _wfopen(argv[1], L"rb");
    if (ef == NULL) {
        say("zconsole_read FAIL: cannot read the expected file\n");
        return 1;
    }
    char want[8192];
    size_t wantn = fread(want, 1, sizeof want - 1, ef);
    fclose(ef);
    want[wantn] = 0;
    while (wantn > 0 && (want[wantn - 1] == '\n' || want[wantn - 1] == '\r')) want[--wantn] = 0;

    size_t cap = 0;
    for (int i = 2; i < argc; i++) cap += wcslen(argv[i]) * 2 + 3;
    wchar_t* cmd = (wchar_t*)malloc((cap + 1) * sizeof(wchar_t));
    size_t at = 0;
    cmd[0] = 0;
    for (int i = 2; i < argc; i++) quote_into(cmd, &at, argv[i]);

    FreeConsole();
    if (!AllocConsole()) {
        say("zconsole_read FAIL: AllocConsole refused\n");
        return 1;
    }
    HANDLE con = CreateFileW(L"CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL,
                             OPEN_EXISTING, 0, NULL);
    COORD size = {200, 100};
    SetConsoleScreenBufferSize(con, size);

    /* AllocConsole leaves standard handles that are redirected alone, and ours
       are ssh's pipes: the program is handed the console's own */
    SECURITY_ATTRIBUTES inherit = {sizeof inherit, NULL, TRUE};
    HANDLE cin = CreateFileW(L"CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &inherit,
                             OPEN_EXISTING, 0, NULL);
    HANDLE cout = CreateFileW(L"CONOUT$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, &inherit,
                              OPEN_EXISTING, 0, NULL);
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = cin;
    si.hStdOutput = cout;
    si.hStdError = cout;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
        say("zconsole_read FAIL: the program did not start\n");
        return 1;
    }
    DWORD code = 1;
    if (WaitForSingleObject(pi.hProcess, 60000) != WAIT_OBJECT_0) {
        TerminateProcess(pi.hProcess, 1);
        say("zconsole_read FAIL: the program did not finish in 60 seconds\n");
        return 1;
    }
    GetExitCodeProcess(pi.hProcess, &code);

    CONSOLE_SCREEN_BUFFER_INFO info;
    GetConsoleScreenBufferInfo(con, &info);
    SHORT rows = info.dwCursorPosition.Y + (info.dwCursorPosition.X > 0 ? 1 : 0);
    SHORT width = info.dwSize.X;
    wchar_t* screen = (wchar_t*)malloc(((size_t)rows * (width + 1) + 1) * sizeof(wchar_t));
    wchar_t* line = (wchar_t*)malloc(((size_t)width + 1) * sizeof(wchar_t));
    size_t sn = 0;
    for (SHORT y = 0; y < rows; y++) {
        COORD from = {0, y};
        DWORD got = 0;
        ReadConsoleOutputCharacterW(con, line, (DWORD)width, from, &got);
        while (got > 0 && line[got - 1] == L' ') got--;
        if (y > 0) screen[sn++] = L'\n';
        memcpy(screen + sn, line, got * sizeof(wchar_t));
        sn += got;
    }
    char* seen = utf8_of(screen, (int)sn);

    int ok = seen != NULL && strcmp(seen, want) == 0 && code == 0;
    FreeConsole();
    if (ok) {
        say("zconsole_read OK\n");
        return 0;
    }
    char head[64];
    snprintf(head, sizeof head, "zconsole_read FAIL: exit %lu; the console shows:\n", (unsigned long)code);
    say(head);
    say(seen != NULL ? seen : "(unreadable)");
    say("\n-- expected:\n");
    say(want);
    say("\n");
    return 1;
}
#else
int main(void) {
    return 0;
}
#endif
