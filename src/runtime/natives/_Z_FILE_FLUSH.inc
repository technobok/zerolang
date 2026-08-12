/* file.flush — no-op for raw file descriptors (POSIX write goes
   directly to the kernel, no userspace buffer to drain). Exists
   so file satisfies the `writer` protocol signature. */
static z_@Result_null_IoError@_t z_@File@_flush(z_@File@_t* f);
static z_@Result_null_IoError@_t z_@File@_flush(z_@File@_t* f) {
    (void)f;
    z_@Result_null_IoError@_t result = {0};
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}


