/* Populate fs for z_io_stat / z_io_lstat: on POSIX from a struct stat -- the
   only behavioral split between them lives in the syscall call site (stat(2)
   follows symlinks, lstat(2) does not) -- and on Windows from the file's handle
   (z_io_win_stat). */
#if defined(_WIN32)
/* seconds since 1970 from a FILETIME's 100ns ticks since 1601 */
static uint64_t z_io_win_unix_seconds(uint64_t ticks) {
    const uint64_t epoch = 116444736000000000ULL;
    return ticks > epoch ? (ticks - epoch) / 10000000ULL : 0;
}

static uint64_t z_io_win_ft(FILETIME ft) {
    return ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
}

/* whether the path's last component ends in an extension Windows runs */
static int z_io_win_runnable(const char* s, size_t n) {
    size_t i = n;
    while (i > 0 && s[i - 1] != '/' && s[i - 1] != '\\') i--;
    const char* dot = NULL;
    for (size_t j = i; j < n; j++) if (s[j] == '.') dot = s + j;
    if (dot == NULL || (size_t)(s + n - dot) != 4) return 0;
    const char* exts[] = { ".exe", ".com", ".bat", ".cmd" };
    for (int k = 0; k < 4; k++) {
        int same = 1;
        for (int c = 0; c < 4; c++) {
            char ch = dot[c];
            if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
            if (ch != exts[k][c]) same = 0;
        }
        if (same) return 1;
    }
    return 0;
}

/* stat (follow) or lstat (not) a path through its handle; 0, or the Win32
   error. kind: a symbolic link or junction is `symlink` under lstat. mode is
   synthesised as POSIX spells it: the type bits; 0444 for a read-only file,
   else 0666; plus 0111 for a directory and for .exe, .com, .bat and .cmd.
   device is the volume serial number, inode the file index, ctime the change
   time. */
static int z_io_win_stat(z_@StringView@_t path, int follow, z_@filestat@_t* fs) {
    DWORD flags = FILE_FLAG_BACKUP_SEMANTICS | (follow ? 0 : FILE_FLAG_OPEN_REPARSE_POINT);
    HANDLE h = z_win_open(path.data, path.size, FILE_READ_ATTRIBUTES, OPEN_EXISTING, flags);
    if (h == INVALID_HANDLE_VALUE) return (int)GetLastError();
    BY_HANDLE_FILE_INFORMATION bi;
    if (!GetFileInformationByHandle(h, &bi)) {
        int e = (int)GetLastError();
        CloseHandle(h);
        return e;
    }
    FILE_ATTRIBUTE_TAG_INFO ti;
    memset(&ti, 0, sizeof ti);
    if (bi.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
        GetFileInformationByHandleEx(h, FileAttributeTagInfo, &ti, sizeof ti);
    }
    FILE_BASIC_INFO basic;
    BOOL has_basic = GetFileInformationByHandleEx(h, FileBasicInfo, &basic, sizeof basic);
    CloseHandle(h);
    int is_dir = (bi.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    int is_link = !follow && (bi.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        && (ti.ReparseTag == IO_REPARSE_TAG_SYMLINK || ti.ReparseTag == IO_REPARSE_TAG_MOUNT_POINT);
    uint32_t mode;
    if (is_link) {
        fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.symlink@;
        mode = 0120000;
    } else if (is_dir) {
        fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.dir@;
        mode = 0040000;
    } else {
        fs->@MEMBER_filestat.kind@@TAG_filekind@ = @ARM_filekind.file@;
        mode = 0100000;
    }
    mode |= (bi.dwFileAttributes & FILE_ATTRIBUTE_READONLY) ? 0444 : 0666;
    if (is_dir || z_io_win_runnable(path.data, path.size)) mode |= 0111;
    fs->@MEMBER_filestat.size@ = is_dir ? 0 : (((uint64_t)bi.nFileSizeHigh << 32) | bi.nFileSizeLow);
    fs->@MEMBER_filestat.mtimeSeconds@ = z_io_win_unix_seconds(z_io_win_ft(bi.ftLastWriteTime));
    fs->@MEMBER_filestat.atimeSeconds@ = z_io_win_unix_seconds(z_io_win_ft(bi.ftLastAccessTime));
    fs->@MEMBER_filestat.ctimeSeconds@ = has_basic
        ? z_io_win_unix_seconds((uint64_t)basic.ChangeTime.QuadPart)
        : z_io_win_unix_seconds(z_io_win_ft(bi.ftLastWriteTime));
    fs->@MEMBER_filestat.mode@ = mode;
    fs->@MEMBER_filestat.device@ = (uint64_t)bi.dwVolumeSerialNumber;
    fs->@MEMBER_filestat.inode@ = ((uint64_t)bi.nFileIndexHigh << 32) | bi.nFileIndexLow;
    fs->@MEMBER_filestat.nlink@ = (uint64_t)bi.nNumberOfLinks;
    return 0;
}
#else
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
#endif
