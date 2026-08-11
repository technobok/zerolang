/* file.close — explicit close that surfaces delayed write errors.
   Marks the file as closed so z_@File@_destroy skips a second close. */
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p);
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p) {
    z_@Result_null_IoError@_t result = {0};
    if (!p || p->closed) {
        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
        result.data = NULL;
        return result;
    }
    int rc = close(p->fd);
    int e = errno;
    p->closed = true;
    return z_io_wrap_null_Result(rc, e);
}

