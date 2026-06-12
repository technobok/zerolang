/* stat(2) follows symlinks; the SYMLINK arm never fires here —
   that arm is reached through z_io_lstat. Returns the filestat
   value by value (not boxed); the compiler-generated result
   destructor frees the ok payload's heap copy. */
static z_Result_filestat_IoError_t z_io_stat(z_StringView_t path);
static z_Result_filestat_IoError_t z_io_stat(z_StringView_t path) {
    z_Result_filestat_IoError_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    struct stat sb;
    int rc = stat(path_cstr, &sb);
    int e = errno;
    free(path_cstr);
    if (rc != 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_filestat_t fs = {0};
    z_io_fill_filestat(&fs, &sb);
    z_filestat_t* boxed = (z_filestat_t*)z_xmalloc(sizeof(z_filestat_t));
    *boxed = fs;
    result.tag = Z_RESULT_FILESTAT_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

