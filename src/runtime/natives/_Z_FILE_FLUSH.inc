/* file.flush — no-op for raw file descriptors (POSIX write goes
   directly to the kernel, no userspace buffer to drain). Exists
   so file satisfies the `writer` protocol signature. */
static z_Result_null_IoError_t z_File_flush(z_File_t* f);
static z_Result_null_IoError_t z_File_flush(z_File_t* f) {
    (void)f;
    z_Result_null_IoError_t result = {0};
    result.tag = Z_RESULT_NULL_IOERROR_TAG_OK;
    result.data = NULL;
    return result;
}

