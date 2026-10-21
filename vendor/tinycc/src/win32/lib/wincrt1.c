//+---------------------------------------------------------------------------

// _UNICODE for tchar.h, UNICODE for API
#include <tchar.h>

#include <windows.h>
#include <stdlib.h>

#define __UNKNOWN_APP    0
#define __CONSOLE_APP    1
#define __GUI_APP        2
void _controlfp(unsigned a, unsigned b);

#ifdef _UNICODE
#define __tgetmainargs __wgetmainargs
#define _twinstart _wwinstart
#define _runtwinmain _runwwinmain
int APIENTRY wWinMain(HINSTANCE, HINSTANCE, LPWSTR, int);
#else
#define __tgetmainargs __getmainargs
#define _twinstart _winstart
#define _runtwinmain _runwinmain
#endif

/* zerolang local patch: the UCRT's startup calls (ucrtbase.dll), in place of
   msvcrt's __getmainargs, __set_app_type and _XcptFilter */
#ifdef _UNICODE
#define _configure_targv _configure_wide_argv
#define _initialize_tenvironment _initialize_wide_environment
#else
#define _configure_targv _configure_narrow_argv
#define _initialize_tenvironment _initialize_narrow_environment
#endif
int __cdecl _configure_targv(int mode); /* 1 as written, 2 wildcards expanded */
int __cdecl _initialize_tenvironment(void);
void __cdecl _set_app_type(int apptype);
int __cdecl _seh_filter_exe(unsigned long code, EXCEPTION_POINTERS *ex);

#include "crtinit.c"

static int go_winmain(TCHAR *arg1)
{
    STARTUPINFO si;
    _TCHAR *szCmd, *p;
    int fShow;
    int retval;

    GetStartupInfo(&si);
    if (si.dwFlags & STARTF_USESHOWWINDOW)
        fShow = si.wShowWindow;
    else
        fShow = SW_SHOWDEFAULT;

    szCmd = NULL, p = GetCommandLine();
    if (arg1)
        szCmd = _tcsstr(p, arg1);
    if (NULL == szCmd)
        szCmd = _tcsdup(__T(""));
    else if (szCmd > p && szCmd[-1] == __T('"'))
        --szCmd;
#if defined __i386__ || defined __x86_64__
    _controlfp(0x10000, 0x30000);
#endif
    run_ctors(__argc, __targv, _tenviron);
    retval = _tWinMain(GetModuleHandle(NULL), NULL, szCmd, fShow);
    run_dtors();
    return retval;
}

static LONG WINAPI catch_sig(EXCEPTION_POINTERS *ex)
{
  return _seh_filter_exe(ex->ExceptionRecord->ExceptionCode, ex);
}

int _twinstart(void)
{
    SetUnhandledExceptionFilter(catch_sig);
    _set_app_type(__GUI_APP);
    _configure_targv(1);
    _initialize_tenvironment();
    exit(go_winmain(__argc > 1 ? __targv[1] : NULL));
}

int _runtwinmain(int argc, /* as tcc passed in */ char **argv)
{
#ifdef UNICODE
    _configure_targv(1);
    _initialize_tenvironment();
    /* may be wrong when tcc has received wildcards (*.c) */
    if (argc < __argc)
        __targv += __argc - argc, __argc = argc;
#else
    __argc = argc, __targv = argv;
#endif
    return go_winmain(__argc > 1 ? __targv[1] : NULL);
}
