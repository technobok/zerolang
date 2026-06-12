/* symlink — create a symbolic link at `link` with `target` as the
   link content. Both args are borrowed views; caller retains.
   EEXIST maps to ioerror.exists so callers can distinguish
   'already there' from other failures. */
static z_Result_null_IoError_t z_io_symlink(
    z_StringView_t target, z_StringView_t link
);
static z_Result_null_IoError_t z_io_symlink(
    z_StringView_t target, z_StringView_t link
) {
    char* target_cstr = z_sv_to_cstr(target);
    char* link_cstr = z_sv_to_cstr(link);
    int rc = symlink(target_cstr, link_cstr);
    int e = errno;
    free(target_cstr);
    free(link_cstr);
    return z_io_wrap_null_Result(rc, e);
}

