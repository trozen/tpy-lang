// Bodies for tpy::stdlib::os (declared in tpy/stdlib/os.hpp). Kept out of the
// header so <filesystem> and <sys/stat.h> are not pulled into every TU that
// calls into os; failed operations map to the matching TPy exception.

#include <tpy/stdlib/os.hpp>

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <string>
#include <string_view>
#include <system_error>
#include <tuple>
#include <vector>

#include <sys/stat.h>
#include <unistd.h>

#include <tpy/core.hpp>

namespace tpy::stdlib::os {

namespace {

std::filesystem::path to_path(std::string_view p) {
    // A TPy str view is not guaranteed null-terminated; copy into a string.
    return std::filesystem::path(std::string(p));
}

// Maps an errno-domain error_code to the OSError subclass CPython raises for
// it (the os-module errno table); anything else surfaces as plain OSError.
[[noreturn]] void raise_fs_error(const std::error_code& ec,
                                 std::string_view op, std::string_view path) {
    const std::string m = ec.message();
    if (ec == std::errc::no_such_file_or_directory)
        ::tpy::raise_file_not_found_error("{}: '{}': {}", op, path, m);
    if (ec == std::errc::file_exists)
        ::tpy::raise_file_exists_error("{}: '{}': {}", op, path, m);
    if (ec == std::errc::not_a_directory)
        ::tpy::raise_not_a_directory_error("{}: '{}': {}", op, path, m);
    if (ec == std::errc::is_a_directory)
        ::tpy::raise_is_a_directory_error("{}: '{}': {}", op, path, m);
    if (ec == std::errc::permission_denied ||
        ec == std::errc::operation_not_permitted)
        ::tpy::raise_permission_error("{}: '{}': {}", op, path, m);
    ::tpy::raise_os_error("{}: '{}': {}", op, path, m);
}

// Raise from the live errno for a failed syscall. MUST be called immediately
// after the syscall with nothing in between (callers hold the path in a named
// local so no destructor runs first): errno is read here, not captured upfront.
[[noreturn]] void raise_errno(std::string_view op, std::string_view path) {
    raise_fs_error(std::error_code(errno, std::generic_category()), op, path);
}

} // namespace

std::string getcwd() {
    std::error_code ec;
    auto p = std::filesystem::current_path(ec);
    if (ec) ::tpy::raise_os_error("getcwd: {}", ec.message());
    return p.string();
}

void chdir(std::string_view path) {
    std::error_code ec;
    std::filesystem::current_path(to_path(path), ec);
    if (ec) raise_fs_error(ec, "chdir", path);
}

std::vector<std::string> listdir(std::string_view path) {
    // Drive the iterator with the non-throwing increment(ec) overload: the
    // range-for form calls the throwing operator++, whose filesystem_error
    // is not a TPy exception and would escape the FFI boundary uncaught.
    std::error_code ec;
    std::filesystem::directory_iterator it(to_path(path), ec);
    if (ec) raise_fs_error(ec, "listdir", path);
    const std::filesystem::directory_iterator end;
    std::vector<std::string> names;
    while (it != end) {
        names.push_back(it->path().filename().string());
        it.increment(ec);
        if (ec) raise_fs_error(ec, "listdir", path);
    }
    return names;
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
        raise_fs_error(std::error_code(err, std::generic_category()),
                       "getsize", path);
    }
    return static_cast<int64_t>(st.st_size);
}

bool env_has(std::string_view key) {
    return std::getenv(std::string(key).c_str()) != nullptr;
}

std::string env_get(std::string_view key) {
    const char* v = std::getenv(std::string(key).c_str());
    return v ? std::string(v) : std::string();
}

// Mutating ops bind the raw POSIX syscalls (mirroring CPython, which wraps the
// same calls) for byte-exact errno parity and to keep os.rmdir/os.remove
// distinct (std::filesystem::remove conflates files and dirs). Each holds the
// path in a named local so nothing runs between the syscall and the errno read.

void mkdir(std::string_view path, int64_t mode) {
    std::string p(path);
    if (::mkdir(p.c_str(), static_cast<::mode_t>(mode)) != 0)
        raise_errno("mkdir", path);
}

void rmdir(std::string_view path) {
    std::string p(path);
    if (::rmdir(p.c_str()) != 0) raise_errno("rmdir", path);
}

void remove(std::string_view path) {
    std::string p(path);
    if (::unlink(p.c_str()) != 0) raise_errno("remove", path);
}

void rename(std::string_view src, std::string_view dst) {
    std::string s(src), d(dst);
    if (::rename(s.c_str(), d.c_str()) != 0) raise_errno("rename", src);
}

void symlink(std::string_view target, std::string_view linkpath) {
    std::string t(target), l(linkpath);
    if (::symlink(t.c_str(), l.c_str()) != 0) raise_errno("symlink", linkpath);
}

std::string readlink(std::string_view path) {
    std::string p(path);
    // ::readlink does not null-terminate and silently truncates at the buffer
    // size, so grow until the result fits (a target can exceed PATH_MAX).
    std::string buf(256, '\0');
    for (;;) {
        ssize_t n = ::readlink(p.c_str(), buf.data(), buf.size());
        if (n < 0) raise_errno("readlink", path);
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

StatTuple to_tuple(const struct ::stat& st) {
    return {static_cast<int64_t>(st.st_mode), static_cast<int64_t>(st.st_ino),
            static_cast<int64_t>(st.st_dev),  static_cast<int64_t>(st.st_nlink),
            static_cast<int64_t>(st.st_uid),  static_cast<int64_t>(st.st_gid),
            static_cast<int64_t>(st.st_size),
            ts_secs(st.st_atim), ts_secs(st.st_mtim), ts_secs(st.st_ctim),
            ts_ns(st.st_atim),   ts_ns(st.st_mtim),   ts_ns(st.st_ctim)};
}

} // namespace

StatTuple stat_raw(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno("stat", path);
    return to_tuple(st);
}

StatTuple lstat_raw(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::lstat(p.c_str(), &st) != 0) raise_errno("lstat", path);
    return to_tuple(st);
}

// os.path stat-backed queries: native float/bool helpers (parallel to
// path_getsize), so os.path needs no dependency on the stat_result type.

double path_getmtime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno("getmtime", path);
    return ts_secs(st.st_mtim);
}

double path_getatime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno("getatime", path);
    return ts_secs(st.st_atim);
}

double path_getctime(std::string_view path) {
    std::string p(path);
    struct ::stat st;
    if (::stat(p.c_str(), &st) != 0) raise_errno("getctime", path);
    return ts_secs(st.st_ctim);
}

bool path_samefile(std::string_view a, std::string_view b) {
    std::string pa(a), pb(b);
    struct ::stat sa, sb;
    if (::stat(pa.c_str(), &sa) != 0) raise_errno("samefile", a);
    if (::stat(pb.c_str(), &sb) != 0) raise_errno("samefile", b);
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

} // namespace tpy::stdlib::os
