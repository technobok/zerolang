/* translate the openmode variant tag to open(2) flags and a default mode, or
   on Windows to z_win_open_fd's three ways */
static z_@Result_File_IoError@_t z_io_open(
    z_@StringView@_t path, z_@openmode@_t mode
) {
    z_@Result_File_IoError@_t result = {0};
#if defined(_WIN32)
    int how = 0;
    switch (mode@TAG_openmode@) {
        case @ARM_openmode.write@:  how = 1; break;
        case @ARM_openmode.append@: how = 2; break;
        default:                    how = 0; break;
    }
    int fd = z_win_open_fd(path.data, path.size, how);
    int e = (int)GetLastError();
#else
    int flags;
    mode_t perm = 0644;
    switch (mode@TAG_openmode@) {
        case @ARM_openmode.read@:   flags = O_RDONLY; break;
        case @ARM_openmode.write@:  flags = O_WRONLY | O_CREAT | O_TRUNC; break;
        case @ARM_openmode.append@: flags = O_WRONLY | O_CREAT | O_APPEND; break;
        default:                    flags = O_RDONLY; break;
    }
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, flags | O_CLOEXEC, perm);
    int e = errno;
    free(path_cstr);
#endif
    if (fd < 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_os_error(e);
        result.tag = @ARM_Result_File_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@File@_t* boxed = (z_@File@_t*)z_xmalloc(sizeof(z_@File@_t));
    boxed->@MEMBER_File.fd@ = (int32_t)fd;
    boxed->@MEMBER_File.open@ = true;
    result.tag = @ARM_Result_File_IoError.ok@;
    result.data = boxed;
    return result;
}


