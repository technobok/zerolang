static z_@Result_u64_IoError@_t z_io_u64_ok(uint64_t v);
static z_@Result_u64_IoError@_t z_io_u64_ok(uint64_t v) {
    z_@Result_u64_IoError@_t result = {0};
    uint64_t* boxed = (uint64_t*)z_xmalloc(sizeof(uint64_t));
    *boxed = v;
    result.tag = Z_RESULT_BOX_U64_IOERROR_TAG_OK;
    result.data = boxed;
    return result;
}

static z_@Result_u64_IoError@_t z_io_u64_err(int saved_errno);
static z_@Result_u64_IoError@_t z_io_u64_err(int saved_errno) {
    z_@Result_u64_IoError@_t result = {0};
    z_@IoError@_t* boxed = (z_@IoError@_t*)z_xmalloc(sizeof(z_@IoError@_t));
    *boxed = z_io_errno_to_IoError(saved_errno);
    result.tag = Z_RESULT_BOX_U64_IOERROR_TAG_ERR;
    result.data = boxed;
    return result;
}

