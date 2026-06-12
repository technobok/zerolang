static z_Result_null_IoError_t z_io_writeText(
    z_StringView_t path, z_StringView_t content
) {
    return z_io_write_common(path, content, O_WRONLY | O_CREAT | O_TRUNC);
}

