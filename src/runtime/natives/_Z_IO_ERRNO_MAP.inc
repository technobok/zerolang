static z_IoError_t z_io_errno_to_IoError(int e) {
    z_IoError_t r = {0};
    r.data = NULL;
    switch (e) {
        case ENOENT:  r.tag = Z_IOERROR_TAG_NOTFOUND; break;
        case EACCES:
        case EPERM:   r.tag = Z_IOERROR_TAG_PERMISSIONDENIED; break;
        case EINTR:   r.tag = Z_IOERROR_TAG_INTERRUPTED; break;
        case EEXIST:  r.tag = Z_IOERROR_TAG_EXISTS; break;
        case EISDIR:  r.tag = Z_IOERROR_TAG_ISDIR; break;
        case ENOTDIR: r.tag = Z_IOERROR_TAG_NOTDIR; break;
        case ENOSPC:  r.tag = Z_IOERROR_TAG_NOSPACE; break;
        default:      r.tag = Z_IOERROR_TAG_OTHER; break;
    }
    return r;
}

