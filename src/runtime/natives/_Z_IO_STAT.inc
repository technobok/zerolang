/* stat(2) follows symlinks; the SYMLINK arm never fires here —
   that arm is reached through z_io_lstat. Returns the filestat
   value by value (not boxed); the compiler-generated result
   destructor frees the ok payload's heap copy. */
static z_@Result_filestat_IoError@_t z_io_stat(z_@StringView@_t path);
static z_@Result_filestat_IoError@_t z_io_stat(z_@StringView@_t path) {
    z_@Result_filestat_IoError@_t result = {0};
    z_@filestat@_t fs = {0};
#if defined(_WIN32)
    int e = z_io_win_stat(path, 1, &fs);
    int rc = e == 0 ? 0 : -1;
#else
    char* path_cstr = z_sv_to_cstr(path);
    struct stat sb;
    int rc = stat(path_cstr, &sb);
    int e = errno;
    free(path_cstr);
    if (rc == 0) z_io_fill_filestat(&fs, &sb);
#endif
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_filestat_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@filestat@_t* boxed = (z_@filestat@_t*)z_xmalloc(sizeof(z_@filestat@_t));
    *boxed = fs;
    result.tag = @ARM_Result_filestat_IoError.ok@;
    result.data = boxed;
    return result;
}


