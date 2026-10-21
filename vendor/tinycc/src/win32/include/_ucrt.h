/*
 * _ucrt.h -- tcc's Windows headers against the Universal CRT (ucrtbase.dll).
 *
 * zerolang local patch (see VERSION.md): this tcc links ucrtbase.dll, the CRT
 * every other Windows compiler uses, in place of msvcrt.dll. The headers below
 * it were written for msvcrt and reach its data through `_imp__` imports the
 * UCRT does not export; each such block is guarded by `#ifndef <name>`, so
 * defining the UCRT form here, before any of them (from _mingw.h), selects it.
 * The functions the UCRT supplies only as header inlines (the printf and scanf
 * families, time(), atexit()) are real functions in libtcc1.a (win32/lib/ucrt.c).
 *
 * This file is part of the zerolang vendored copy of TinyCC and is licensed
 * under the GNU Lesser General Public License, version 2.1, as TinyCC is.
 */
#ifndef _TCC_UCRT_H
#define _TCC_UCRT_H

#define _UCRT 1

/* FILE is opaque; the standard streams are __acrt_iob_func(0..2) */
#ifndef _FILE_DEFINED
struct _iobuf { void *_Placeholder; };
typedef struct _iobuf FILE;
#define _FILE_DEFINED
#endif
FILE *__cdecl __acrt_iob_func(unsigned _Ix);
#define _STDSTREAM_DEFINED
#define stdin  (__acrt_iob_func(0))
#define stdout (__acrt_iob_func(1))
#define stderr (__acrt_iob_func(2))

/* the process globals, through their accessors */
int *__cdecl __p___argc(void);
char ***__cdecl __p___argv(void);
unsigned short ***__cdecl __p___wargv(void);
char ***__cdecl __p__environ(void);
unsigned short ***__cdecl __p__wenviron(void);
char **__cdecl __p__pgmptr(void);
unsigned short **__cdecl __p__wpgmptr(void);
int *__cdecl __p__fmode(void);
#define __argc    (*__p___argc())
#define __argv    (*__p___argv())
#define __wargv   (*(wchar_t ***)__p___wargv())
#define _environ  (*__p__environ())
#define _wenviron (*(wchar_t ***)__p__wenviron())
#define _pgmptr   (*__p__pgmptr())
#define _wpgmptr  (*(wchar_t **)__p__wpgmptr())
#define _fmode    (*__p__fmode())

/* locale data, through its accessors */
int __cdecl ___mb_cur_max_func(void);
const unsigned short *__cdecl __pctype_func(void);
const unsigned short *__cdecl __pwctype_func(void);
#define MB_CUR_MAX     ___mb_cur_max_func()
#define __mb_cur_max   ___mb_cur_max_func()
#define __PCTYPE_FUNC  __pctype_func()
#define _pctype        (__pctype_func())
#define _CRT_WCTYPEDATA_DEFINED
#define _pwctype       (__pwctype_func())

/* HUGE_VAL without msvcrt's _HUGE */
#define _HUGE (1.0 / 0.0)

/* errno: the POSIX values the UCRT adds to msvcrt's */
#define EADDRINUSE      100
#define EADDRNOTAVAIL   101
#define EAFNOSUPPORT    102
#define EALREADY        103
#define EBADMSG         104
#define ECANCELED       105
#define ECONNABORTED    106
#define ECONNREFUSED    107
#define ECONNRESET      108
#define EDESTADDRREQ    109
#define EHOSTUNREACH    110
#define EIDRM           111
#define EINPROGRESS     112
#define EISCONN         113
#define ELOOP           114
#define EMSGSIZE        115
#define ENETDOWN        116
#define ENETRESET       117
#define ENETUNREACH     118
#define ENOBUFS         119
#define ENODATA         120
#define ENOLINK         121
#define ENOMSG          122
#define ENOPROTOOPT     123
#define ENOSR           124
#define ENOSTR          125
#define ENOTCONN        126
#define ENOTRECOVERABLE 127
#define ENOTSOCK        128
#define ENOTSUP         129
#define EOPNOTSUPP      130
#define EOTHER          131
#define EOVERFLOW       132
#define EOWNERDEAD      133
#define EPROTO          134
#define EPROTONOSUPPORT 135
#define EPROTOTYPE      136
#define ETIME           137
#define ETIMEDOUT       138
#define ETXTBSY         139
#define EWOULDBLOCK     140

/* rand_s, which the UCRT declares under _CRT_RAND_S */
int __cdecl rand_s(unsigned int *_RandomValue);

#endif
