#pragma once

// Filesystem + environment primitives backing the `os` / `os.path` stdlib
// modules. Declarations only: the bodies (and the heavy <filesystem> /
// <sys/stat.h> includes they need) live in src/stdlib/os_impl.cpp so those
// system headers do not leak into every TU that calls into os.

#include <cstdint>
#include <string>
#include <string_view>
#include <span>
#include <tuple>
#include <vector>

#include "tpy/buffer_types.hpp"

namespace tpy::stdlib::os {

std::string getcwd();
void chdir(std::string_view path);
std::vector<std::string> listdir(std::string_view path);

// os.scandir backing: each entry's name + a normalized kind from the readdir
// d_type (0 unknown, 1 dir, 2 file, 3 symlink), skipping "." / "..". DirEntry
// resolves kind 0/3 via stat. `.`/`..` excluded like CPython.
std::vector<std::tuple<std::string, int64_t>> scandir_raw(std::string_view path);

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

// os.path.realpath: absolute path with symlinks resolved. strict=false
// (weakly_canonical): resolves the existing prefix, lexically normalizes a
// non-existent tail, never fails on a missing path (best-effort lexical
// fallback on a filesystem error). strict=true (canonical): the whole path
// must exist; raises the matching OSError subclass (FileNotFoundError on a
// missing component) otherwise, like CPython realpath(strict=True).
std::string path_realpath(std::string_view path, bool strict = false);

// env_get reads libc; the os.environ snapshot is built once at startup by
// pairing environ_keys() with a env_get() per key. setenv/unsetenv mutate the
// libc environment (os.putenv/unsetenv, and the os.environ write path).
std::vector<std::string> environ_keys();
std::string env_get(std::string_view key);
void setenv(std::string_view key, std::string_view value);
void unsetenv(std::string_view key);

// Home directory of the running user (getpwuid of getuid) / of a named user
// (getpwnam). Empty string on not-found or error -- os.path.expanduser treats
// that as "leave the ~ verbatim", matching CPython's KeyError handling.
std::string current_home();
std::string user_home(std::string_view name);

// Low-level file descriptor I/O (raw POSIX; each raises raise_errno on -1).
// read returns up to n bytes (short reads possible, like CPython); write
// returns the count written.
int64_t open_fd(std::string_view path, int64_t flags, int64_t mode);
void close_fd(int64_t fd);
::tpy::Bytes read_fd(int64_t fd, int64_t n);
int64_t write_fd(int64_t fd, std::span<const uint8_t> data);
int64_t lseek_fd(int64_t fd, int64_t pos, int64_t how);
std::tuple<int64_t, int64_t> pipe_fd();
int64_t dup_fd(int64_t fd);
int64_t dup2_fd(int64_t fd, int64_t fd2);
std::tuple<int64_t, int64_t, int64_t, int64_t, int64_t, int64_t, int64_t,
           double, double, double, int64_t, int64_t, int64_t>
fstat_fd(int64_t fd);

// File metadata mutation + randomness (raw POSIX; raise_errno on failure).
void chmod_path(std::string_view path, int64_t mode);
void chown_path(std::string_view path, int64_t uid, int64_t gid);
void utime_path(std::string_view path, double atime, double mtime);
bool access_path(std::string_view path, int64_t mode);
::tpy::Bytes urandom(int64_t n);

// open()/lseek()/access() flag, whence, and mode constants, exposed to TPy via
// native_global. Sourced from the real macros so the platform-varying values
// (O_CREAT family differs Linux vs macOS) are correct; non-const so the TPy
// native_global binding's `extern int64_t` matches. The TPy-side names (os.O_*,
// os.SEEK_*, os.*_OK) would collide with these libc macros if emitted as C++
// symbols, which is exactly why they bind to these safe-named globals instead.
extern int64_t kc_o_rdonly;
extern int64_t kc_o_wronly;
extern int64_t kc_o_rdwr;
extern int64_t kc_o_creat;
extern int64_t kc_o_excl;
extern int64_t kc_o_trunc;
extern int64_t kc_o_append;
extern int64_t kc_seek_set;
extern int64_t kc_seek_cur;
extern int64_t kc_seek_end;
extern int64_t kc_f_ok;
extern int64_t kc_r_ok;
extern int64_t kc_w_ok;
extern int64_t kc_x_ok;

// int32 SEEK_* for io.seek (its whence is Int32, not the Int64 os.lseek uses);
// same POSIX values, separate width.
extern int32_t kc_seek_set32;
extern int32_t kc_seek_cur32;
extern int32_t kc_seek_end32;

// Process identity + small system queries. getpid/getppid/getuid/... can't fail
// (POSIX). getlogin raises OSError on failure (no controlling terminal); umask
// returns the previous mask; cpu_count returns 0 when indeterminate (the TPy
// facade maps that to None); isatty never raises.
int64_t getpid();
int64_t getppid();
int64_t getuid();
int64_t geteuid();
int64_t getgid();
int64_t getegid();
std::string getlogin();
int64_t umask(int64_t mask);
int64_t cpu_count();
std::string strerror(int64_t code);
bool isatty(int64_t fd);

// Hardlink, truncation, durability (raw POSIX; raise_errno on -1).
void link_path(std::string_view src, std::string_view dst);
void truncate_path(std::string_view path, int64_t length);
void ftruncate_fd(int64_t fd, int64_t length);
void fsync_fd(int64_t fd);

// os.get_terminal_size: (columns, lines) via TIOCGWINSZ; raise_errno if the fd
// is not a terminal (matching CPython, which raises OSError).
std::tuple<int64_t, int64_t> terminal_size_raw(int64_t fd);

} // namespace tpy::stdlib::os
