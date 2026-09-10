// Bodies for tpy::stdlib::os (declared in tpy/stdlib/os.hpp). Kept out of the
// header so <filesystem> and <sys/stat.h> are not pulled into every TU that
// calls into os; failed operations map to the matching TPy exception.

#include <tpy/stdlib/os.hpp>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <filesystem>
#include <string>
#include <string_view>
#include <system_error>
#include <tuple>
#include <vector>

#include <dirent.h>
#include <fcntl.h>
#include <pwd.h>
#include <sys/ioctl.h>
#include <sys/random.h>
#include <sys/stat.h>
#include <unistd.h>

// macOS does not expose `environ` to a shared library / non-main TU; the
// supported access is `*_NSGetEnviron()`. Linux/BSD declare it in <unistd.h>.
#if defined(__APPLE__)
#include <crt_externs.h>
#endif

#include <tpy/core.hpp>

namespace tpy::stdlib::os {

namespace {

std::filesystem::path to_path(std::string_view p) {
    // A TPy str view is not guaranteed null-terminated; copy into a string.
    return std::filesystem::path(std::string(p));
}

// Raise the errno-keyed OSError subclass with CPython-exact message and
// attributes ("[Errno N] strerror[: 'path'[ -> 'path2']]"); the errno ->
// subclass table is the shared tpy::raise_mapped_os_error. The operation
// name is deliberately absent: CPython's os errors don't carry it.
[[noreturn]] void raise_fs_error(const std::error_code& ec, std::string_view path,
                                 std::string_view path2 = "") {
    ::tpy::raise_mapped_os_error(static_cast<int32_t>(ec.value()), ec.message(),
                                 path, path2);
}

// Raise from the live errno for a failed syscall. MUST be called immediately
// after the syscall with nothing in between (callers hold the path in a named
// local so no destructor runs first): errno is read here, not captured upfront.
[[noreturn]] void raise_errno(std::string_view path = "", std::string_view path2 = "") {
    raise_fs_error(std::error_code(errno, std::generic_category()), path, path2);
}

} // namespace

std::string getcwd() {
    std::error_code ec;
    auto p = std::filesystem::current_path(ec);
    // Through the mapped table like every other site: CPython's errno remap
    // applies to getcwd too (a deleted cwd raises FileNotFoundError there).
    if (ec) raise_fs_error(ec, "");
    return p.string();
}

void chdir(std::string_view path) {
    std::error_code ec;
    std::filesystem::current_path(to_path(path), ec);
    if (ec) raise_fs_error(ec, path);
}

std::vector<std::string> listdir(std::string_view path) {
    // Drive the iterator with the non-throwing increment(ec) overload: the
    // range-for form calls the throwing operator++, whose filesystem_error
    // is not a TPy exception and would escape the FFI boundary uncaught.
    std::error_code ec;
    std::filesystem::directory_iterator it(to_path(path), ec);
    if (ec) raise_fs_error(ec, path);
    const std::filesystem::directory_iterator end;
    std::vector<std::string> names;
    while (it != end) {
        names.push_back(it->path().filename().string());
        it.increment(ec);
        if (ec) raise_fs_error(ec, path);
    }
    return names;
}

std::vector<std::tuple<std::string, int64_t>> scandir_raw(std::string_view path) {
    // Raw readdir (not directory_iterator) to surface d_type without a stat.
    DIR* dir = ::opendir(std::string(path).c_str());
    if (dir == nullptr) raise_errno(path);
    // RAII close so the descriptor is released even if an allocation below
    // throws (raise_fs_error throws too -- it must run after the close).
    struct DirGuard {
        DIR* d;
        ~DirGuard() { ::closedir(d); }
    } guard{dir};
    std::vector<std::tuple<std::string, int64_t>> entries;
    // readdir returns null at end-of-stream AND on error; errno disambiguates,
    // so clear it before each call and check after the loop.
    errno = 0;
    while (struct ::dirent* ent = ::readdir(dir)) {
        std::string name(ent->d_name);
        if (name != "." && name != "..") {
            int64_t kind = 0;
            switch (ent->d_type) {
                case DT_DIR: kind = 1; break;
                case DT_REG: kind = 2; break;
                case DT_LNK: kind = 3; break;
                default:     kind = 0; break;  // DT_UNKNOWN etc. -> DirEntry stats
            }
            entries.emplace_back(std::move(name), kind);
        }
        errno = 0;
    }
    if (errno != 0) {
        // errno is still the readdir error here -- the guard's closedir runs
        // only on scope exit, after this throw.
        raise_fs_error(std::error_code(errno, std::generic_category()), path);
    }
    return entries;
}

bool path_exists(std::string_view path) {
    std::error_code ec;
    return std::filesystem::exists(to_path(path), ec);
}

bool path_lexists(std::string_view path) {
    // symlink_status does not follow the link, so a broken symlink still
    // reports as existing -- which is exactly os.path.lexists.
    std::error_code ec;
    return std::filesystem::exists(std::filesystem::symlink_status(to_path(path), ec));
}

bool path_isfile(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_regular_file(to_path(path), ec);
}

bool path_isdir(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_directory(to_path(path), ec);
}

bool path_islink(std::string_view path) {
    std::error_code ec;
    return std::filesystem::is_symlink(to_path(path), ec);
}

int64_t path_getsize(std::string_view path) {
    // os.path.getsize is os.stat(path).st_size: it follows symlinks and reports
    // a size for any path type (a directory's st_size, not an error), unlike
    // std::filesystem::file_size which is defined only for regular files.
    // st_size is a signed off_t, so no overflow on the cast.
    struct ::stat st;
    if (::stat(std::string(path).c_str(), &st) != 0) {
        // Capture errno before constructing the error_code: the arguments to
        // error_code(...) have unsequenced evaluation, so reading errno inline
        // could race a side effect in the other argument.
        int err = errno;
        raise_fs_error(std::error_code(err, std::generic_category()), path);
    }
    return static_cast<int64_t>(st.st_size);
}

std::string path_realpath(std::string_view path, bool strict) {
    // Absolutize first: weakly_canonical leaves a fully-non-existent *relative*
    // path (and "") relative, but CPython realpath always makes it absolute
    // (relative to cwd). absolute() handles the empty path as cwd.
    // CPython realpath("") is cwd, same as realpath("."); absolute("") errors.
    auto input = path.empty() ? std::filesystem::path(".") : to_path(path);
    std::error_code ec;
    auto abs = std::filesystem::absolute(input, ec);
    if (ec) abs = input;
    ec.clear();
    if (strict) {
        // canonical requires the whole path to exist; map its error to the
        // matching OSError subclass (ENOENT -> FileNotFoundError, etc.).
        auto resolved = std::filesystem::canonical(abs, ec);
        if (ec) raise_fs_error(ec, path);
        return resolved.string();
    }
    auto resolved = std::filesystem::weakly_canonical(abs, ec);
    if (ec) {
        // weakly_canonical fails only on a real error (ELOOP, EACCES mid-path),
        // not a missing path. CPython realpath(strict=False) returns a partial
        // best-effort there; approximate with the lexical absolute path.
        resolved = abs.lexically_normal();
    }
    return resolved.string();
}

// The keys of every "KEY=VALUE" entry in the process environment, in libc
// order. Used once at startup to seed the os.environ snapshot (the values come
// from env_get per key). An entry without '=' is skipped (malformed).
std::vector<std::string> environ_keys() {
    std::vector<std::string> keys;
#if defined(__APPLE__)
    char** envp = *_NSGetEnviron();
#else
    char** envp = ::environ;
#endif
    for (char** e = envp; e && *e; ++e) {
        std::string_view entry(*e);
        auto eq = entry.find('=');
        if (eq != std::string_view::npos) {
            keys.emplace_back(entry.substr(0, eq));
        }
    }
    return keys;
}

std::string env_get(std::string_view key) {
    const char* v = std::getenv(std::string(key).c_str());
    return v ? std::string(v) : std::string();
}

void setenv(std::string_view key, std::string_view value) {
    // EINVAL (key contains '=' or is empty) / ENOMEM -> OSError, as CPython
    // raises for os.putenv / os.environ[k] = v; never silently no-op.
    // No filename: CPython's putenv raises via bare posix_error (the env
    // key is not a filename).
    if (::setenv(std::string(key).c_str(), std::string(value).c_str(), 1) != 0) {
        raise_errno();
    }
}

void unsetenv(std::string_view key) {
    if (::unsetenv(std::string(key).c_str()) != 0) raise_errno();
}

// getpwuid_r/getpwnam_r need a caller-provided buffer; _SC_GETPW_R_SIZE_MAX is
// only a hint (can be -1), so fall back to a generous size.
static std::string pw_dir_or_empty(const struct ::passwd* result) {
    if (result == nullptr || result->pw_dir == nullptr) return std::string();
    return std::string(result->pw_dir);
}

std::string current_home() {
    long hint = ::sysconf(_SC_GETPW_R_SIZE_MAX);
    std::string buf(hint > 0 ? static_cast<size_t>(hint) : 16384, '\0');
    struct ::passwd pw;
    struct ::passwd* result = nullptr;
    if (::getpwuid_r(::getuid(), &pw, buf.data(), buf.size(), &result) != 0) {
        return std::string();
    }
    return pw_dir_or_empty(result);
}

std::string user_home(std::string_view name) {
    long hint = ::sysconf(_SC_GETPW_R_SIZE_MAX);
    std::string buf(hint > 0 ? static_cast<size_t>(hint) : 16384, '\0');
    struct ::passwd pw;
    struct ::passwd* result = nullptr;
    if (::getpwnam_r(std::string(name).c_str(), &pw, buf.data(), buf.size(),
                     &result) != 0) {
        return std::string();
    }
    return pw_dir_or_empty(result);
}

// Mutating ops bind the raw POSIX syscalls (mirroring CPython, which wraps the
// same calls) for byte-exact errno parity and to keep os.rmdir/os.remove
// distinct (std::filesystem::remove conflates files and dirs). Each holds the
// path in a named local so nothing runs between the syscall and the errno read.

void mkdir(std::string_view path, int64_t mode) {
    std::string p(path);
    if (::mkdir(p.c_str(), static_cast<::mode_t>(mode)) != 0)
        raise_errno(path);
}

void rmdir(std::string_view path) {
    std::string p(path);
    if (::rmdir(p.c_str()) != 0) raise_errno(path);
}

void remove(std::string_view path) {
    std::string p(path);
    if (::unlink(p.c_str()) != 0) raise_errno(path);
}

void rename(std::string_view src, std::string_view dst) {
    std::string s(src), d(dst);
    if (::rename(s.c_str(), d.c_str()) != 0) raise_errno(src, dst);
}

void symlink(std::string_view target, std::string_view linkpath) {
    std::string t(target), l(linkpath);
    if (::symlink(t.c_str(), l.c_str()) != 0) raise_errno(target, linkpath);
}

std::string readlink(std::string_view path) {
    std::string p(path);
    // ::readlink does not null-terminate and silently truncates at the buffer
    // size, so grow until the result fits (a target can exceed PATH_MAX).
    std::string buf(256, '\0');
    for (;;) {
        ssize_t n = ::readlink(p.c_str(), buf.data(), buf.size());
        if (n < 0) raise_errno(path);
        if (static_cast<size_t>(n) < buf.size()) {
            buf.resize(static_cast<size_t>(n));
            return buf;
        }
        buf.resize(buf.size() * 2);
    }
}

namespace {

// st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size,
// st_atime, st_mtime, st_ctime (float secs), st_atime_ns, st_mtime_ns,
// st_ctime_ns -- the flat tuple the TPy stat_result wrapper unpacks.
using StatTuple = std::tuple<int64_t, int64_t, int64_t, int64_t, int64_t,
                             int64_t, int64_t, double, double, double,
                             int64_t, int64_t, int64_t>;

double ts_secs(const struct timespec& t) {
    return static_cast<double>(t.tv_sec) + static_cast<double>(t.tv_nsec) / 1e9;
}

int64_t ts_ns(const struct timespec& t) {
    return static_cast<int64_t>(t.tv_sec) * 1000000000LL +
           static_cast<int64_t>(t.tv_nsec);
}

// Apple names the struct stat sub-second members st_{a,m,c}timespec; Linux and
// POSIX-2008 BSDs use st_{a,m,c}tim. Localize the spelling difference here.
#if defined(__APPLE__)
const struct timespec& stat_atime(const struct ::stat& s) { return s.st_atimespec; }
const struct timespec& stat_mtime(const struct ::stat& s) { return s.st_mtimespec; }
const struct timespec& stat_ctime(const struct ::stat& s) { return s.st_ctimespec; }
#else
const struct timespec& stat_atime(const struct ::stat& s) { return s.st_atim; }
const struct timespec& stat_mtime(const struct ::stat& s) { return s.st_mtim; }
const struct timespec& stat_ctime(const struct ::stat& s) { return s.st_ctim; }
#endif

StatTuple to_tuple(const struct ::stat& st) {
    return {static_cast<int64_t>(st.st_mode), static_cast<int64_t>(st.st_ino),
            static_cast<int64_t>(st.st_dev),  static_cast<int64_t>(st.st_nlink),
            static_cast<int64_t>(st.st_uid),  static_cast<int64_t>(st.st_gid),
            static_cast<int64_t>(st.st_size),
            ts_secs(stat_atime(st)), ts_secs(stat_mtime(st)), ts_secs(stat_ctime(st)),
            ts_ns(stat_atime(st)),   ts_ns(stat_mtime(st)),   ts_ns(stat_ctime(st))};
}

} // namespace

StatTuple stat_raw(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno(path);
    return to_tuple(st);
}

StatTuple lstat_raw(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::lstat(p.c_str(), &st) != 0) raise_errno(path);
    return to_tuple(st);
}

// os.path stat-backed queries: native float/bool helpers (parallel to
// path_getsize), so os.path needs no dependency on the stat_result type.

double path_getmtime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno(path);
    return ts_secs(stat_mtime(st));
}

double path_getatime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno(path);
    return ts_secs(stat_atime(st));
}

double path_getctime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno(path);
    return ts_secs(stat_ctime(st));
}

bool path_samefile(std::string_view a, std::string_view b) {
    std::string pa(a), pb(b);
    struct ::stat sa, sb;
    if (::stat(pa.c_str(), &sa) != 0) raise_errno(a);
    if (::stat(pb.c_str(), &sb) != 0) raise_errno(b);
    return sa.st_ino == sb.st_ino && sa.st_dev == sb.st_dev;
}

bool path_ismount(std::string_view path) {
    // A mount point's st_dev differs from its parent's (or its parent is
    // itself -- the filesystem root); matches os.path.ismount.
    std::string p(path);
    struct ::stat s1, s2;
    if (::lstat(p.c_str(), &s1) != 0) return false;
    if (S_ISLNK(s1.st_mode)) return false;
    std::string parent = p + "/..";
    if (::lstat(parent.c_str(), &s2) != 0) return false;
    return s1.st_dev != s2.st_dev || s1.st_ino == s2.st_ino;
}


// --- Low-level file descriptor I/O -----------------------------------------

int64_t open_fd(std::string_view path, int64_t flags, int64_t mode) {
    std::string p(path);
    int fd = ::open(p.c_str(), static_cast<int>(flags),
                    static_cast<::mode_t>(mode));
    if (fd < 0) raise_errno(path);
    return fd;
}

void close_fd(int64_t fd) {
    if (::close(static_cast<int>(fd)) != 0) raise_errno();
}

::tpy::Bytes read_fd(int64_t fd, int64_t n) {
    ::tpy::Bytes buf(n > 0 ? static_cast<size_t>(n) : 0);
    if (n <= 0) return buf;
    ssize_t got = ::read(static_cast<int>(fd), buf.data(),
                         static_cast<size_t>(n));
    if (got < 0) raise_errno();
    buf.resize(static_cast<size_t>(got));  // short read -> shrink to actual
    return buf;
}

int64_t write_fd(int64_t fd, std::span<const uint8_t> data) {
    ssize_t n = ::write(static_cast<int>(fd), data.data(), data.size());
    if (n < 0) raise_errno();
    return n;
}

int64_t lseek_fd(int64_t fd, int64_t pos, int64_t how) {
    off_t r = ::lseek(static_cast<int>(fd), static_cast<off_t>(pos),
                      static_cast<int>(how));
    if (r < 0) raise_errno();
    return static_cast<int64_t>(r);
}

std::tuple<int64_t, int64_t> pipe_fd() {
    int fds[2];
    if (::pipe(fds) != 0) raise_errno();
    return {fds[0], fds[1]};
}

int64_t dup_fd(int64_t fd) {
    int r = ::dup(static_cast<int>(fd));
    if (r < 0) raise_errno();
    return r;
}

int64_t dup2_fd(int64_t fd, int64_t fd2) {
    int r = ::dup2(static_cast<int>(fd), static_cast<int>(fd2));
    if (r < 0) raise_errno();
    return r;
}

StatTuple fstat_fd(int64_t fd) {
    struct ::stat st;
    if (::fstat(static_cast<int>(fd), &st) != 0) raise_errno();
    return to_tuple(st);
}

// Constant values from the real macros (see os.hpp for why TPy binds these via
// native_global rather than naming the constants directly).
int64_t kc_o_rdonly = O_RDONLY;
int64_t kc_o_wronly = O_WRONLY;
int64_t kc_o_rdwr   = O_RDWR;
int64_t kc_o_creat  = O_CREAT;
int64_t kc_o_excl   = O_EXCL;
int64_t kc_o_trunc  = O_TRUNC;
int64_t kc_o_append = O_APPEND;
int64_t kc_seek_set = SEEK_SET;
int64_t kc_seek_cur = SEEK_CUR;
int64_t kc_seek_end = SEEK_END;
int64_t kc_f_ok     = F_OK;
int64_t kc_r_ok     = R_OK;
int64_t kc_w_ok     = W_OK;
int64_t kc_x_ok     = X_OK;
int32_t kc_seek_set32 = SEEK_SET;
int32_t kc_seek_cur32 = SEEK_CUR;
int32_t kc_seek_end32 = SEEK_END;

// --- Process identity + small system queries -------------------------------

int64_t getpid()  { return ::getpid(); }
int64_t getppid() { return ::getppid(); }
int64_t getuid()  { return ::getuid(); }
int64_t geteuid() { return ::geteuid(); }
int64_t getgid()  { return ::getgid(); }
int64_t getegid() { return ::getegid(); }

std::string getlogin() {
    const char* name = ::getlogin();
    if (name == nullptr) raise_errno();
    return std::string(name);
}

int64_t umask(int64_t mask) {
    return static_cast<int64_t>(::umask(static_cast<::mode_t>(mask)));
}

int64_t cpu_count() {
    // 0 when indeterminate; the TPy facade turns that into None (CPython).
    return static_cast<int64_t>(std::thread::hardware_concurrency());
}

std::string strerror(int64_t code) {
    return std::string(std::strerror(static_cast<int>(code)));
}

bool isatty(int64_t fd) {
    return ::isatty(static_cast<int>(fd)) != 0;
}

void link_path(std::string_view src, std::string_view dst) {
    std::string s(src), d(dst);
    if (::link(s.c_str(), d.c_str()) != 0) raise_errno(src, dst);
}

void truncate_path(std::string_view path, int64_t length) {
    std::string p(path);
    if (::truncate(p.c_str(), static_cast<off_t>(length)) != 0)
        raise_errno(path);
}

void ftruncate_fd(int64_t fd, int64_t length) {
    if (::ftruncate(static_cast<int>(fd), static_cast<off_t>(length)) != 0)
        raise_errno();
}

void fsync_fd(int64_t fd) {
    if (::fsync(static_cast<int>(fd)) != 0) raise_errno();
}

std::tuple<int64_t, int64_t> terminal_size_raw(int64_t fd) {
    struct ::winsize ws{};
    if (::ioctl(static_cast<int>(fd), TIOCGWINSZ, &ws) != 0)
        raise_errno();
    return {ws.ws_col, ws.ws_row};
}

// --- Metadata + randomness -------------------------------------------------

void chmod_path(std::string_view path, int64_t mode) {
    std::string p(path);
    if (::chmod(p.c_str(), static_cast<::mode_t>(mode)) != 0)
        raise_errno(path);
}

void chown_path(std::string_view path, int64_t uid, int64_t gid) {
    std::string p(path);
    if (::chown(p.c_str(), static_cast<::uid_t>(uid),
                static_cast<::gid_t>(gid)) != 0)
        raise_errno(path);
}

void utime_path(std::string_view path, double atime, double mtime) {
    std::string p(path);
    auto to_ts = [](double t) -> struct timespec {
        // floor (not truncate-toward-zero) so the fractional part -- and thus
        // tv_nsec -- is non-negative for negative (pre-1970) timestamps too;
        // utimensat rejects a negative tv_nsec with EINVAL.
        double secs_f = std::floor(t);
        auto secs = static_cast<time_t>(secs_f);
        long ns = static_cast<long>((t - secs_f) * 1e9);
        if (ns > 999999999) ns = 999999999;  // guard rounding at the boundary
        return {secs, ns};
    };
    struct timespec times[2] = {to_ts(atime), to_ts(mtime)};
    if (::utimensat(AT_FDCWD, p.c_str(), times, 0) != 0)
        raise_errno(path);
}

bool access_path(std::string_view path, int64_t mode) {
    // access returns False on any failure (not just EACCES), matching CPython.
    return ::access(std::string(path).c_str(), static_cast<int>(mode)) == 0;
}

::tpy::Bytes urandom(int64_t n) {
    ::tpy::Bytes buf(n > 0 ? static_cast<size_t>(n) : 0);
    size_t off = 0;
    while (off < buf.size()) {
        // getentropy caps at 256 bytes per call on every platform.
        size_t chunk = std::min<size_t>(256, buf.size() - off);
        if (::getentropy(buf.data() + off, chunk) != 0)
            raise_errno();
        off += chunk;
    }
    return buf;
}

} // namespace tpy::stdlib::os

extern "C" {

// File-domain errno constant values for the `errno` facade module, sourced
// from <errno.h> here for the same reason socket_impl.cpp exposes the
// network-domain set: platform-correct values without pulling system headers
// into TPy-generated TUs. Non-const so the type matches the `extern int32_t`
// the native_global decl emits.
std::int32_t tpy_const_enoent = ENOENT;
std::int32_t tpy_const_eexist = EEXIST;
std::int32_t tpy_const_eacces = EACCES;
std::int32_t tpy_const_eperm = EPERM;
std::int32_t tpy_const_eisdir = EISDIR;
std::int32_t tpy_const_enotdir = ENOTDIR;
std::int32_t tpy_const_ebadf = EBADF;
std::int32_t tpy_const_etimedout = ETIMEDOUT;

}  // extern "C"
