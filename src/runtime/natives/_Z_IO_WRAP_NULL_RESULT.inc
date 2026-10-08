static z_@Result_null_IoError@_t z_io_wrap_null_Result(int rc, int saved_errno) {
    z_@Result_null_IoError@_t result = {0};
    if (rc == 0) {
        result.tag = @ARM_Result_null_IoError.ok@;
        result.data = NULL;
        return result;
    }
    z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
    *boxed = z_io_errno_to_IoError(saved_errno);
    result.tag = @ARM_Result_null_IoError.err@;
    result.data = boxed;
    return result;
}


