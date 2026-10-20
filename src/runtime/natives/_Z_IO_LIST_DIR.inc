/* Enumerate directory entries via opendir/readdir/closedir, or on
   Windows FindFirstFileExW/FindNextFileW over `path\*`.
   Returns bare entry names (not full paths), skipping `.` and
   `..`, in filesystem order. The list is constructed on stack and
   then boxed heap-side so the result union's void* data can
   carry it; the compiler-generated result destructor calls
   z_@List_String@_destroy on the ok payload, which in turn frees
   each entry's z_@String@_t. */
static z_@Result_List_String_IoError@_t z_io_listDir(z_@StringView@_t path);
static z_@Result_List_String_IoError@_t z_io_listDir(z_@StringView@_t path) {
    z_@Result_List_String_IoError@_t result = {0};
#if defined(_WIN32)
    /* path\* -- a root or a path ending in a separator takes just the * */
    char* pat = (char*)z_xmalloc(path.size + 3);
    if (path.size > 0) memcpy(pat, path.data, path.size);
    size_t pn = path.size;
    if (pn > 0 && pat[pn - 1] != '/' && pat[pn - 1] != '\\' && pat[pn - 1] != ':') pat[pn++] = '\\';
    pat[pn++] = '*';
    wchar_t* w = path.size > 0 ? z_win_path(pat, pn) : NULL;
    free(pat);
    WIN32_FIND_DATAW fd;
    HANDLE d = INVALID_HANDLE_VALUE;
    if (w != NULL) {
        d = FindFirstFileExW(w, FindExInfoBasic, &fd, FindExSearchNameMatch, NULL, FIND_FIRST_EX_LARGE_FETCH);
    } else if (path.size == 0) {
        SetLastError(ERROR_PATH_NOT_FOUND);
    }
    int e = (int)GetLastError();
    free(w);
    int empty = 0;
    if (d == INVALID_HANDLE_VALUE && path.size > 0) {
        /* what the path itself is decides: a directory with nothing in it (a
           volume's root holds no `.`), or a file, which is notdir as on POSIX */
        wchar_t* wp = z_win_path(path.data, path.size);
        DWORD a = wp != NULL ? GetFileAttributesW(wp) : INVALID_FILE_ATTRIBUTES;
        free(wp);
        if (a != INVALID_FILE_ATTRIBUTES) {
            if (a & FILE_ATTRIBUTE_DIRECTORY) {
                if (e == ERROR_FILE_NOT_FOUND) empty = 1;
            } else {
                e = ERROR_DIRECTORY;
            }
        }
    }
    if (d == INVALID_HANDLE_VALUE && !empty) {
#else
    char* path_cstr = z_sv_to_cstr(path);
    DIR* d = opendir(path_cstr);
    int e = errno;
    free(path_cstr);
    if (!d) {
#endif
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_List_String_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@List_String@_t list = z_@List_String@_create((uint64_t)0);
#if defined(_WIN32)
    if (!empty) {
        do {
            const wchar_t* nm = fd.cFileName;
            if (nm[0] == L'.' && (nm[1] == 0 || (nm[1] == L'.' && nm[2] == 0))) continue;
            char* u = z_win_utf8(nm, -1, NULL);
            if (u == NULL) continue;
            z_@List_String@_append(&list, z_@String@_new(u));
            free(u);
        } while (FindNextFileW(d, &fd));
        FindClose(d);
    }
#else
    struct dirent* entry;
    errno = 0;
    while ((entry = readdir(d)) != NULL) {
        const char* name = entry->d_name;
        if (name[0] == '.' && (name[1] == '\0' ||
            (name[1] == '.' && name[2] == '\0'))) continue;
        z_@List_String@_append(&list, z_@String@_new(name));
    }
    closedir(d);
#endif
    z_@List_String@_t* boxed = (z_@List_String@_t*)z_xmalloc(sizeof(z_@List_String@_t));
    *boxed = list;
    result.tag = @ARM_Result_List_String_IoError.ok@;
    result.data = boxed;
    return result;
}


