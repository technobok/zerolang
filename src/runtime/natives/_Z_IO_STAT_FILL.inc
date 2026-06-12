/* Populate fs from a struct stat. Shared by z_io_stat / z_io_lstat —
   the only behavioral split between them lives in the syscall call
   site (stat(2) follows symlinks, lstat(2) does not). */
static void z_io_fill_filestat(z_filestat_t* fs, const struct stat* sb);
static void z_io_fill_filestat(z_filestat_t* fs, const struct stat* sb) {
    if (S_ISREG(sb->st_mode))       fs->kind.tag = Z_FILEKIND_TAG_FILE;
    else if (S_ISDIR(sb->st_mode))  fs->kind.tag = Z_FILEKIND_TAG_DIR;
    else if (S_ISLNK(sb->st_mode))  fs->kind.tag = Z_FILEKIND_TAG_SYMLINK;
    else                            fs->kind.tag = Z_FILEKIND_TAG_OTHER;
    fs->size = (uint64_t)sb->st_size;
    fs->mtimeSeconds = (uint64_t)sb->st_mtime;
    fs->atimeSeconds = (uint64_t)sb->st_atime;
    fs->ctimeSeconds = (uint64_t)sb->st_ctime;
    fs->mode = (uint32_t)sb->st_mode;
    fs->device = (uint64_t)sb->st_dev;
    fs->inode = (uint64_t)sb->st_ino;
    fs->nlink = (uint64_t)sb->st_nlink;
}

