/* translate openmode variant tag to open(2) flags + default mode */
static z_@Result_File_IoError@_t z_io_open(
    z_@StringView@_t path, z_@openmode@_t mode
) {
    z_@Result_File_IoError@_t result = {0};
    int flags;
    mode_t perm = 0644;
    switch (mode@TAG_openmode@) {
        case @ARM_openmode.read@:   flags = O_RDONLY; break;
        case @ARM_openmode.write@:  flags = O_WRONLY | O_CREAT | O_TRUNC; break;
        case @ARM_openmode.append@: flags = O_WRONLY | O_CREAT | O_APPEND; break;
        default:                    flags = O_RDONLY; break;
    }
    char* path_cstr = z_sv_to_cstr(path);
    int fd = open(path_cstr, flags, perm);
    int e = errno;
    free(path_cstr);
    if (fd < 0) {
        z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
        *boxed = z_io_errno_to_IoError(e);
        result.tag = @ARM_Result_File_IoError.err@;
        result.data = boxed;
        return result;
    }
    z_@File@_t* boxed = (z_@File@_t*)z_xmalloc(sizeof(z_@File@_t));
    boxed->@MEMBER_File.fd@ = (int32_t)fd;
    boxed->@MEMBER_File.closed@ = false;
    result.tag = @ARM_Result_File_IoError.ok@;
    result.data = boxed;
    return result;
}


