static z_Result_u64_IoError_t z_io_u64_ok(uint64_t v);
static z_Result_u64_IoError_t z_io_u64_ok(uint64_t v) {
    z_Result_u64_IoError_t result = {0};
    uint64_t* boxed = (uint64_t*)z_xmalloc(sizeof(uint64_t));
    *boxed = v;
    result.tag = Z_RESULT_U64_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

static z_Result_u64_IoError_t z_io_u64_err(int saved_errno);
static z_Result_u64_IoError_t z_io_u64_err(int saved_errno) {
    z_Result_u64_IoError_t result = {0};
    z_IoError_t* boxed = (z_IoError_t*)z_xmalloc(sizeof(z_IoError_t));
    *boxed = z_io_errno_to_IoError(saved_errno);
    result.tag = Z_RESULT_U64_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}

