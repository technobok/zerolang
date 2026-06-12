/* translate openmode variant tag to open(2) flags + default mode */
static z_Result_File_IoError_t z_io_open(
    z_StringView_t path, z_openmode_t mode
) {
    z_Result_File_IoError_t result = {0};
    int flags;
    mode_t perm = 0644;
    switch (mode.tag) {
        case Z_OPENMODE_TAG_READ:   flags = O_RDONLY; break;
        case Z_OPENMODE_TAG_WRITE:  flags = O_WRONLY | O_CREAT | O_TRUNC; break;
        case Z_OPENMODE_TAG_APPEND: flags = O_WRONLY | O_CREAT | O_APPEND; break;
        default:                    flags = O_RDONLY; break;
    }
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, flags, perm);
    int e = errno;
    free(path_cstr);
    if (fd < 0) {
        z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = Z_RESULT_FILE_IOERROR_TAG_ERR;
        result.data = boxed;
        return result;
    }
    z_File_t* boxed = (z_File_t*)z_xmalloc(sizeof(z_File_t));
    boxed->fd = (int32_t)fd;
    boxed->closed = false;
    result.tag = Z_RESULT_FILE_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

