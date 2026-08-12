/* lstat(2) — like stat but does not follow symlinks. The SYMLINK
   arm of filekind fires here when the target path is a symlink. */
static z_@Result_filestat_IoError@_t z_io_lstat(z_@StringView@_t path);
static z_@Result_filestat_IoError@_t z_io_lstat(z_@StringView@_t path) {
    z_@Result_filestat_IoError@_t result = {0};
    char* path_cstr = z_sv_to_cstr(path);
    struct stat sb;
    int rc = lstat(path_cstr, &sb);
    int e = errno;
    free(path_cstr);
    if (rc != 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_BOX_FILESTAT_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_@filestat@_t fs = {0};
    z_io_fill_filestat(&fs, &sb);
    z_@filestat@_t* boxed = (z_@filestat@_t*)z_xmalloc(sizeof(z_@filestat@_t));
    *boxed = fs;
    result.tag = Z_RESULT_BOX_FILESTAT_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}


