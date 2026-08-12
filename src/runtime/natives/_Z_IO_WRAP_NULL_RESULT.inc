static z_@Result_null_IoError@_t z_io_wrap_null_Result(int rc, int saved_errno) {
    z_@Result_null_IoError@_t result = {0};
    if (rc == 0) {
        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
        result.data = NULL;
        return result;
    }
    z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
    *boxed = z_io_errno_to_IoError(saved_errno);
    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}


