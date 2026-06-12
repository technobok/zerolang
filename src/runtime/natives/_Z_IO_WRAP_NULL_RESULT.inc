static z_Result_null_IoError_t z_io_wrap_null_Result(int rc, int saved_errno) {
    z_Result_null_IoError_t result = {0};
    if (rc == 0) {
        result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
        result.data = NULL;
        return result;
    }
    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
    *boxed = z_io_errno_to_IoError(saved_errno);
    result.tag = Z_RESULT_NULL_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}

