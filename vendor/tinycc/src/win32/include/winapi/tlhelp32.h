/*
 * tlhelp32.h -- the process snapshot part of the Tool Help library.
 *
 * zerolang local patch (see VERSION.md): this reduced winapi had no
 * tlhelp32.h; zerolang's runtime walks the process list for a parent pid.
 * Declarations as the Windows SDK documents them. Part of the zerolang
 * vendored copy of TinyCC, licensed under the LGPL 2.1 as TinyCC is.
 */
#ifndef _INC_TOOLHELP32
#define _INC_TOOLHELP32
#include <windows.h>

#define TH32CS_SNAPHEAPLIST 0x00000001
#define TH32CS_SNAPPROCESS  0x00000002
#define TH32CS_SNAPTHREAD   0x00000004
#define TH32CS_SNAPMODULE   0x00000008

typedef struct tagPROCESSENTRY32W {
  DWORD dwSize;
  DWORD cntUsage;
  DWORD th32ProcessID;
  ULONG_PTR th32DefaultHeapID;
  DWORD th32ModuleID;
  DWORD cntThreads;
  DWORD th32ParentProcessID;
  LONG pcPriClassBase;
  DWORD dwFlags;
  WCHAR szExeFile[MAX_PATH];
} PROCESSENTRY32W,*PPROCESSENTRY32W,*LPPROCESSENTRY32W;

WINBASEAPI HANDLE WINAPI CreateToolhelp32Snapshot(DWORD dwFlags,DWORD th32ProcessID);
WINBASEAPI WINBOOL WINAPI Process32FirstW(HANDLE hSnapshot,LPPROCESSENTRY32W lppe);
WINBASEAPI WINBOOL WINAPI Process32NextW(HANDLE hSnapshot,LPPROCESSENTRY32W lppe);
#endif
