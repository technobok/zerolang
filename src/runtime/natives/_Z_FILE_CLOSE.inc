/* file.close — explicit close that surfaces delayed write errors.
   Marks the file as closed so z_@File@_destroy skips a second close. */
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p);
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p) {
    z_@Result_null_IoError@_t result = {0};
    if (!p || p->@MEMBER_File.closed@) {
        result.tag = @ARM_Result_null_IoError.ok@;
        result.data = NULL;
        return result;
    }
    int rc = close(p->@MEMBER_File.fd@);
    int e = errno;
    p->@MEMBER_File.closed@ = true;
    return z_io_wrap_null_Result(rc, e);
}


