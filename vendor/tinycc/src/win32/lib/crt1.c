// =============================================
// crt1.c

// _UNICODE for tchar.h, UNICODE for API
#include <tchar.h>

#include <windows.h>
#include <stdio.h>
#include <stdlib.h>

#define _UNKNOWN_APP    0
#define _CONSOLE_APP    1
#define _GUI_APP        2

#define _MCW_PC         0x00030000 // Precision Control
#define _PC_24          0x00020000 // 24 bits
#define _PC_53          0x00010000 // 53 bits
#define _PC_64          0x00000000 // 64 bits

#ifdef _UNICODE
#define __tgetmainargs __wgetmainargs
#define _tstart _wstart
#define _tmain wmain
#define _runtmain _runwmain
#else
#define __tgetmainargs __getmainargs
#define _tstart _start
#define _tmain main
#define _runtmain _runmain
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
unsigned int __cdecl _controlfp(unsigned int new_value, unsigned int mask);
extern int _tmain(int argc, _TCHAR * argv[], _TCHAR * env[]);

#include "crtinit.c"

/* Allow command-line globbing with "int _dowildcard = 1;" in the user source */
int _dowildcard;

static LONG WINAPI catch_sig(EXCEPTION_POINTERS *ex)
{
  return _seh_filter_exe(ex->ExceptionRecord->ExceptionCode, ex);
}

void _tstart(void)
{
    int ret;

    SetUnhandledExceptionFilter(catch_sig);
    // Sets the current application type
    _set_app_type(_CONSOLE_APP);

    // Set default FP precision to 53 bits (8-byte double)
    // _MCW_PC (Precision control) is not supported on ARM
#if defined __i386__ || defined __x86_64__
    _controlfp(_PC_53, _MCW_PC);
#endif

    _configure_targv(_dowildcard ? 2 : 1);
    _initialize_tenvironment();
    run_ctors(__argc, __targv, _tenviron);
    ret = _tmain(__argc, __targv, _tenviron);
    run_dtors();
    exit(ret);
}

// =============================================
// for 'tcc -run ,,,'

__attribute__((weak)) extern int __run_on_exit();

int _runtmain(int argc, /* as tcc passed in */ char **argv)
{
    int ret;
#if defined UNICODE || defined __aarch64__
    _configure_targv(1);
    _initialize_tenvironment();
#endif
#ifdef UNICODE
    /* may be wrong when tcc has received wildcards (*.c) */
    if (argc < __argc) {
        __targv += __argc - argc;
        __argc = argc;
    }
#else
    __argc = argc;
    __targv = argv;
#endif
#if defined __i386__ || defined __x86_64__
    _controlfp(_PC_53, _MCW_PC);
#endif
    run_ctors(__argc, __targv, _tenviron);
    ret = _tmain(__argc, __targv, _tenviron);
    fflush(stdout);
    fflush(stderr);
    run_dtors();
    __run_on_exit(ret);
    return ret;
}

// =============================================
