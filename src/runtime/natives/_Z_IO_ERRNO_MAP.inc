static z_@IoError@_t z_io_errno_to_IoError(int e) {
    z_@IoError@_t r = {0};
    r.data = NULL;
    switch (e) {
        case ENOENT:  r.tag = @ARM_IoError.notfound@; break;
        case EACCES:
        case EPERM:   r.tag = @ARM_IoError.permissiondenied@; break;
        case EINTR:   r.tag = @ARM_IoError.interrupted@; break;
        case EEXIST:  r.tag = @ARM_IoError.exists@; break;
        case EISDIR:  r.tag = @ARM_IoError.isdir@; break;
        case ENOTDIR: r.tag = @ARM_IoError.notdir@; break;
        case ENOSPC:  r.tag = @ARM_IoError.nospace@; break;
        default:      r.tag = @ARM_IoError.other@; break;
    }
    return r;
}


