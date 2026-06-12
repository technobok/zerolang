/* Enumerate directory entries via opendir/readdir/closedir.
   Returns bare entry names (not full paths), skipping `.` and
   `..`, in filesystem order. The list is constructed on stack and
   then boxed heap-side so the result union's void* data can
   carry it; the compiler-generated result destructor calls
   z_List_String_destroy on the ok payload, which in turn frees
   each entry's z_String_t. */
static z_Result_List_String_IoError_t z_io_listDir(z_StringView_t path);
static z_Result_List_String_IoError_t z_io_listDir(z_StringView_t path) {
    z_Result_List_String_IoError_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    DIR* d = opendir(path_cstr);
    int e = errno;
    free(path_cstr);
    if (!d) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_List_String_t list = z_List_String_create((uint64_t)0);
    struct dirent* entry;
    errno = 0;
    while ((entry = readdir(d)) != NULL) {
        const char* name = entry->d_name;
        if (name[0] == '.' && (name[1] == '\0' ||
            (name[1] == '.' && name[2] == '\0'))) continue;
        z_List_String_append(&list, z_String_new(name));
    }
    closedir(d);
    z_List_String_t* boxed = (z_List_String_t*)z_xmalloc(sizeof(z_List_String_t));
    *boxed = list;
    result.tag = Z_RESULT_LIST_STRING_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

