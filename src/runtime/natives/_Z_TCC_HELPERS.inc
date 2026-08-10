/* Shared by tcc.available and tcc.compileToExe: the libtcc entry points and
   the dlopen that finds them.

   libtcc is LOADED, never linked. That is a licence requirement, not a
   preference -- tinycc is LGPL-2.1 inside a permissive tree -- and
   `make static-tcc-guard` fails the build if any tcc symbol reaches a
   binary. So the names below are strings, resolved at the point of use;
   nothing here creates a link-time dependency, and a zc built without a
   tcc beside it is a zc that reports 127 rather than one that fails to
   start.

   The library lives at <tccDir>/libtcc.so, the same directory that holds
   the runtime payload tcc_set_lib_path needs. One directory, one answer. */
#include <dlfcn.h>
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

/* diagnostics go to stderr verbatim, so a program reports the same text
   whichever mode built it. */
static void z_tcc_on_error(void* opaque, const char* msg) {
    (void)opaque;
    fprintf(stderr, "%s\n", msg);
}

static char* z_tcc_lib_path(z_StringView_t tccDir) {
    char* dir = z_sv_to_cstr(tccDir);
    size_t n = strlen(dir);
    char* p = (char*)z_xmalloc(n + sizeof("/libtcc.so"));
    memcpy(p, dir, n);
    memcpy(p + n, "/libtcc.so", sizeof("/libtcc.so"));
    free(dir);
    return p;
}

/* 1 when every entry point resolved. A partial load is a failed load: a
   libtcc missing one of these is not one this code can drive. */
static int z_tcc_open(z_tcc_api_t* a, z_StringView_t tccDir) {
    char* path = z_tcc_lib_path(tccDir);
    memset(a, 0, sizeof(*a));
    a->handle = dlopen(path, RTLD_NOW | RTLD_LOCAL);
    free(path);
    if (!a->handle) return 0;
    *(void**)&a->nw = dlsym(a->handle, "tcc_new");
    *(void**)&a->del = dlsym(a->handle, "tcc_delete");
    *(void**)&a->set_lib_path = dlsym(a->handle, "tcc_set_lib_path");
    *(void**)&a->set_error_func = dlsym(a->handle, "tcc_set_error_func");
    *(void**)&a->set_options = dlsym(a->handle, "tcc_set_options");
    *(void**)&a->set_output_type = dlsym(a->handle, "tcc_set_output_type");
    *(void**)&a->add_file = dlsym(a->handle, "tcc_add_file");
    *(void**)&a->add_library = dlsym(a->handle, "tcc_add_library");
    *(void**)&a->output_file = dlsym(a->handle, "tcc_output_file");
    if (a->nw && a->del && a->set_lib_path && a->set_error_func
        && a->set_options && a->set_output_type && a->add_file
        && a->add_library && a->output_file) {
        return 1;
    }
    dlclose(a->handle);
    a->handle = NULL;
    return 0;
}

static void z_tcc_close(z_tcc_api_t* a) {
    if (a->handle) dlclose(a->handle);
    a->handle = NULL;
}
