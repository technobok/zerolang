static z_@Result_null_IoError@_t z_io_appendText(
    z_@StringView@_t path, z_@StringView@_t content
) {
    return z_io_write_common(path, content, 2);
}


