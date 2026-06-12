/* file.close — explicit close that surfaces delayed write errors.
   Marks the file as closed so z_File_destroy skips a second close. */
static z_Result_null_IoError_t z_File_close(z_File_t* p);
static z_Result_null_IoError_t z_File_close(z_File_t* p) {
    z_Result_null_IoError_t result = {0};
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

