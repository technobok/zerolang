/* Shared by tcc.available and tcc.compileToExe: the libtcc entry points and
   the dlopen (LoadLibraryW on Windows) that finds them.

   libtcc is LOADED, never linked. That is a licence requirement, not a
   preference -- tinycc is LGPL-2.1 inside a permissive tree -- and
   `make static-tcc-guard` fails the build if any tcc symbol reaches a
   binary. So the names below are strings, resolved at the point of use;
   nothing here creates a link-time dependency, and a zc built without a
   tcc beside it is a zc that reports 127 rather than one that fails to
   start.

   The library lives in <tccDir> -- libtcc.so, or libtcc.dll on Windows --
   the same directory that holds the runtime payload tcc_set_lib_path needs.
   One directory, one answer. */
#if defined(_WIN32)
#define Z_TCC_LIB_FILE "\\libtcc.dll"
#else
#include <dlfcn.h>
#define Z_TCC_LIB_FILE "/libtcc.so"
#endif
typedef struct TCCState z_tcc_State;
typedef void z_tcc_ErrorFunc(void* opaque, const char* msg);

typedef struct {
    void* handle;
    z_tcc_State* (*nw)(void);
    void (*del)(z_tcc_State*);
    void (*set_lib_path)(z_tcc_State*, const char*);
    void (*set_error_func)(z_tcc_State*, void*, z_tcc_ErrorFunc*);
    int (*set_options)(z_tcc_State*, const char*);
    int (*set_output_type)(z_tcc_State*, int);
    int (*add_file)(z_tcc_State*, const char*);
    int (*add_library)(z_tcc_State*, const char*);
    int (*output_file)(z_tcc_State*, const char*);
} z_tcc_api_t;

#define Z_TCC_OUTPUT_EXE 2
#define Z_TCC_OUTPUT_OBJ 3

/* diagnostics go to stderr verbatim, so a program reports the same text
   whichever mode built it. */
static void z_tcc_on_error(void* opaque, const char* msg) {
    (void)opaque;
    fprintf(stderr, "%s\n", msg);
}

static char* z_tcc_lib_path(z_@StringView@_t tccDir) {
    char* dir = z_sv_to_cstr(tccDir);
    size_t n = strlen(dir);
    char* p = (char*)z_xmalloc(n + sizeof(Z_TCC_LIB_FILE));
    memcpy(p, dir, n);
    memcpy(p + n, Z_TCC_LIB_FILE, sizeof(Z_TCC_LIB_FILE));
    free(dir);
    return p;
}

/* the address of `name` in the loaded library, NULL when it has none */
static void* z_tcc_sym(void* handle, const char* name) {
#if defined(_WIN32)
    /* ISO C has no conversion from a function pointer to void*: the bytes are
       copied, which is what dlsym's own result is on the other systems */
    FARPROC p = GetProcAddress((HMODULE)handle, name);
    void* r;
    memcpy(&r, &p, sizeof r);
    return r;
#else
    return dlsym(handle, name);
#endif
}

/* 1 when every entry point resolved. A partial load is a failed load: a
   libtcc missing one of these is not one this code can drive. */
static void z_tcc_close(z_tcc_api_t* a);

static int z_tcc_open(z_tcc_api_t* a, z_@StringView@_t tccDir) {
    memset(a, 0, sizeof(*a));
    char* path = z_tcc_lib_path(tccDir);
#if defined(_WIN32)
    wchar_t* wpath = z_win_path(path, strlen(path));
    a->handle = wpath ? (void*)LoadLibraryW(wpath) : NULL;
    free(wpath);
#else
    a->handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
#endif
    free(path);
    if (!a->handle) return 0;
    *(void**)&a->nw = z_tcc_sym(a->handle, "tcc_new");
    *(void**)&a->del = z_tcc_sym(a->handle, "tcc_delete");
    *(void**)&a->set_lib_path = z_tcc_sym(a->handle, "tcc_set_lib_path");
    *(void**)&a->set_error_func = z_tcc_sym(a->handle, "tcc_set_error_func");
    *(void**)&a->set_options = z_tcc_sym(a->handle, "tcc_set_options");
    *(void**)&a->set_output_type = z_tcc_sym(a->handle, "tcc_set_output_type");
    *(void**)&a->add_file = z_tcc_sym(a->handle, "tcc_add_file");
    *(void**)&a->add_library = z_tcc_sym(a->handle, "tcc_add_library");
    *(void**)&a->output_file = z_tcc_sym(a->handle, "tcc_output_file");
    if (a->nw && a->del && a->set_lib_path && a->set_error_func
        && a->set_options && a->set_output_type && a->add_file
        && a->add_library && a->output_file) {
        return 1;
    }
    z_tcc_close(a);
    return 0;
}

static void z_tcc_close(z_tcc_api_t* a) {
#if defined(_WIN32)
    if (a->handle) FreeLibrary((HMODULE)a->handle);
#else
    if (a->handle) dlclose(a->handle);
#endif
    a->handle = NULL;
}

