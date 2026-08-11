/* file.seek — reposition the fd head. Maps seekorigin to the
   matching POSIX whence constant. Returns the new absolute
   position measured from the start of the file. */
static z_@Result_u64_IoError@_t z_@File@_seek(
    z_@File@_t* f, int64_t off, z_@seekorigin@_t origin
);
static z_@Result_u64_IoError@_t z_@File@_seek(
    z_@File@_t* f, int64_t off, z_@seekorigin@_t origin
) {
    int whence;
    switch (origin.tag) {
        case Z_SEEKORIGIN_TAG_START:   whence = SEEK_SET; break;
        case Z_SEEKORIGIN_TAG_CURRENT: whence = SEEK_CUR; break;
        case Z_SEEKORIGIN_TAG_END:     whence = SEEK_END; break;
        default:                       whence = SEEK_SET; break;
    }
    off_t pos = lseek(f->fd, (off_t)off, whence);
    if (pos < 0) return z_io_u64_err(errno);
    return z_io_u64_ok((uint64_t)pos);
}

