/* file.seek — reposition the fd head. Maps seekorigin to the
   matching POSIX whence constant. Returns the new absolute
   position measured from the start of the file. */
static z_@ResultVR_u64_IoError@_t z_@File@_seek(
    z_@File@_t* f, int64_t off, z_@seekorigin@_t origin
);
static z_@ResultVR_u64_IoError@_t z_@File@_seek(
    z_@File@_t* f, int64_t off, z_@seekorigin@_t origin
) {
    int whence;
    switch (origin@TAG_seekorigin@) {
        case @ARM_seekorigin.start@:   whence = SEEK_SET; break;
        case @ARM_seekorigin.current@: whence = SEEK_CUR; break;
        case @ARM_seekorigin.end@:     whence = SEEK_END; break;
        default:                       whence = SEEK_SET; break;
    }
    off_t pos = lseek(f->fd, (off_t)off, whence);
    if (pos < 0) return z_io_u64_err(errno);
    return z_io_u64_ok((uint64_t)pos);
}


