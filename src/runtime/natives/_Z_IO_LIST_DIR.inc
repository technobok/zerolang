/* Enumerate directory entries via opendir/readdir/closedir.
   Returns bare entry names (not full paths), skipping `.` and
   `..`, in filesystem order. The list is constructed on stack and
   then boxed heap-side so the result union's void* data can
   carry it; the compiler-generated result destructor calls
   z_@List_String@_destroy on the ok payload, which in turn frees
   each entry's z_@String@_t. */
static z_@Result_List_String_IoError@_t z_io_listDir(z_@StringView@_t path);
static z_@Result_List_String_IoError@_t z_io_listDir(z_@StringView@_t path) {
    z_@Result_List_String_IoError@_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    DIR* d = opendir(path_cstr);
    int e = errno;
    free(path_cstr);
    if (!d) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_LISTREF_STRING_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_@List_String@_t list = z_@List_String@_create((uint64_t)0);
    struct dirent* entry;
    errno = 0;
    while ((entry = readdir(d)) != NULL) {
        const char* name = entry->d_name;
        if (name[0] == '.' && (name[1] == '\0' ||
            (name[1] == '.' && name[2] == '\0'))) continue;
        z_@List_String@_append(&list, z_@String@_new(name));
    }
    closedir(d);
    z_@List_String@_t* boxed = (z_@List_String@_t*)z_xmalloc(sizeof(z_@List_String@_t));
    *boxed = list;
    result.tag = Z_RESULT_LISTREF_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}


