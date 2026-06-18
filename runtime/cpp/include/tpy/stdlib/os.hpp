#pragma once

// Filesystem + environment primitives backing the `os` / `os.path` stdlib
// modules. Declarations only: the bodies (and the heavy <filesystem> /
// <sys/stat.h> includes they need) live in src/stdlib/os_impl.cpp so those
// system headers do not leak into every TU that calls into os.

#include <cstdint>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

namespace tpy::stdlib::os {

std::string getcwd();
void chdir(std::string_view path);
std::vector<std::string> listdir(std::string_view path);

// Mutating ops (raw POSIX syscalls; errors map to the OSError subclass table).
void mkdir(std::string_view path, int64_t mode);
void rmdir(std::string_view path);
void remove(std::string_view path);
void rename(std::string_view src, std::string_view dst);
void symlink(std::string_view target, std::string_view linkpath);
std::string readlink(std::string_view path);

// os.stat / lstat: a flat 13-field tuple the TPy stat_result wrapper unpacks
// (st_mode, st_ino, st_dev, st_nlink, st_uid, st_gid, st_size, st_atime,
// st_mtime, st_ctime, st_atime_ns, st_mtime_ns, st_ctime_ns).
std::tuple<int64_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t,
           double, double, double, int64_t, int64_t, int64_t>
stat_raw(std::string_view path);
std::tuple<int64_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t,
           double, double, double, int64_t, int64_t, int64_t>
lstat_raw(std::string_view path);

double path_getmtime(std::string_view path);
double path_getatime(std::string_view path);
double path_getctime(std::string_view path);
bool path_samefile(std::string_view a, std::string_view b);
bool path_ismount(std::string_view path);

// Status-query predicates swallow OS errors and report False (matching
// os.path.exists/isfile/isdir/islink/lexists).
bool path_exists(std::string_view path);
bool path_lexists(std::string_view path);
bool path_isfile(std::string_view path);
bool path_isdir(std::string_view path);
bool path_islink(std::string_view path);

// getsize raises OSError for a missing path (unlike the predicates).
int64_t path_getsize(std::string_view path);

bool env_has(std::string_view key);
std::string env_get(std::string_view key);

} // namespace tpy::stdlib::os
