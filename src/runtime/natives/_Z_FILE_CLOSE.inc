/* file.close — explicit close that surfaces delayed write errors.
   Marks the file as closed so z_@File@_destroy skips a second close. */
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p);
static z_@Result_null_IoError@_t z_@File@_close(z_@File@_t* p) {
    z_@Result_null_IoError@_t result = {0};
    if (!p || !p->@MEMBER_File.open@) {
        result.tag = @ARM_Result_null_IoError.ok@;
        result.data = NULL;
        return result;
    }
#if defined(_WIN32)
    int rc = z_win_close(p->@MEMBER_File.fd@);
    int e = (int)GetLastError();
#else
    int rc = close(p->@MEMBER_File.fd@);
    int e = errno;
#endif
    p->@MEMBER_File.open@ = false;
    return z_io_wrap_null_Result(rc, e);
}


