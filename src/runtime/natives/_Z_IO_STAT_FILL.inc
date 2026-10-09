/* Populate fs from a struct stat. Shared by z_io_stat / z_io_lstat —
   the only behavioral split between them lives in the syscall call
   site (stat(2) follows symlinks, lstat(2) does not). */
static void z_io_fill_filestat(z_@filestat@_t* fs, const struct stat* sb);
static void z_io_fill_filestat(z_@filestat@_t* fs, const struct stat* sb) {
    if (S_ISREG(sb->st_mode))       fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.file@;
    else if (S_ISDIR(sb->st_mode))  fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.dir@;
    else if (S_ISLNK(sb->st_mode))  fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.symlink@;
    else                            fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.other@;
    fs->@MEMBER_filestat.size@ = (uint64_t)sb->st_size;
    fs->@MEMBER_filestat.mtimeSeconds@ = (uint64_t)sb->st_mtime;
    fs->@MEMBER_filestat.atimeSeconds@ = (uint64_t)sb->st_atime;
    fs->@MEMBER_filestat.ctimeSeconds@ = (uint64_t)sb->st_ctime;
    fs->@MEMBER_filestat.mode@ = (uint32_t)sb->st_mode;
    fs->@MEMBER_filestat.device@ = (uint64_t)sb->st_dev;
    fs->@MEMBER_filestat.inode@ = (uint64_t)sb->st_ino;
    fs->@MEMBER_filestat.nlink@ = (uint64_t)sb->st_nlink;
}


