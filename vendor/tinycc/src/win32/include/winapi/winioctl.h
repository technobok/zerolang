/*
 * winioctl.h -- the device I/O control codes zerolang's runtime uses.
 *
 * zerolang local patch (see VERSION.md): this reduced winapi had no
 * winioctl.h; the runtime reads a symbolic link's target with
 * FSCTL_GET_REPARSE_POINT. Values as the Windows SDK documents them. Part of
 * the zerolang vendored copy of TinyCC, licensed under the LGPL 2.1 as TinyCC
 * is.
 */
#ifndef _WINIOCTL_
#define _WINIOCTL_
#include <windows.h>

#define FSCTL_GET_REPARSE_POINT 0x000900a8
#define FSCTL_SET_REPARSE_POINT 0x000900a4
#define FSCTL_DELETE_REPARSE_POINT 0x000900ac
#endif
