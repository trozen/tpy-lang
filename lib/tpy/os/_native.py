# os._native -- raw @native bindings to the tpy::stdlib::os C++ helpers.
# A leaf module (imports nothing from the os package) so both os/__init__ and
# os/path can share these stubs without os.path -- which has module-level
# constants and so cannot participate in a cycle -- importing os/__init__.
# Internal: import via `os` / `os.path`, not directly.
# tpy: native_module
# tpy: cpp_namespace("tpystd::os")
# tpy: include("<tpy/stdlib/os.hpp>")
from tpy import int64, Own
from tpy.extern import native


@native("tpy::stdlib::os::getcwd")
def getcwd() -> str: ...


@native("tpy::stdlib::os::chdir")
def chdir(path: str) -> None: ...


@native("tpy::stdlib::os::listdir")
def listdir(path: str) -> Own[list[str]]: ...


# (name, kind) per entry; kind 0 unknown / 1 dir / 2 file / 3 symlink.
@native("tpy::stdlib::os::scandir_raw")
def scandir_raw(path: str) -> Own[list[tuple[str, int64]]]: ...


@native("tpy::stdlib::os::mkdir")
def mkdir(path: str, mode: int64) -> None: ...


@native("tpy::stdlib::os::rmdir")
def rmdir(path: str) -> None: ...


@native("tpy::stdlib::os::remove")
def remove(path: str) -> None: ...


@native("tpy::stdlib::os::rename")
def rename(src: str, dst: str) -> None: ...


@native("tpy::stdlib::os::symlink")
def symlink(target: str, linkpath: str) -> None: ...


@native("tpy::stdlib::os::readlink")
def readlink(path: str) -> str: ...


# 13-field stat tuple (see os/__init__ stat_result for the field order). The
# type is spelled inline -- a @native return annotation does not resolve a
# module-level type alias.
@native("tpy::stdlib::os::stat_raw")
def stat_raw(path: str) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


@native("tpy::stdlib::os::lstat_raw")
def lstat_raw(path: str) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


@native("tpy::stdlib::os::path_getmtime")
def path_getmtime(path: str) -> float: ...


@native("tpy::stdlib::os::path_getatime")
def path_getatime(path: str) -> float: ...


@native("tpy::stdlib::os::path_getctime")
def path_getctime(path: str) -> float: ...


@native("tpy::stdlib::os::path_samefile")
def path_samefile(a: str, b: str) -> bool: ...


@native("tpy::stdlib::os::path_ismount")
def path_ismount(path: str) -> bool: ...


@native("tpy::stdlib::os::environ_keys")
def environ_keys() -> Own[list[str]]: ...


@native("tpy::stdlib::os::env_get")
def env_get(key: str) -> str: ...


@native("tpy::stdlib::os::setenv")
def setenv(key: str, value: str) -> None: ...


@native("tpy::stdlib::os::unsetenv")
def unsetenv(key: str) -> None: ...


# pwd home-dir lookups for os.path.expanduser (empty str if not found).
@native("tpy::stdlib::os::current_home")
def current_home() -> str: ...


@native("tpy::stdlib::os::user_home")
def user_home(name: str) -> str: ...


# Low-level fd I/O (raw POSIX; each raises OSError on failure).
@native("tpy::stdlib::os::open_fd")
def open_fd(path: str, flags: int64, mode: int64) -> int64: ...


@native("tpy::stdlib::os::close_fd")
def close_fd(fd: int64) -> None: ...


@native("tpy::stdlib::os::read_fd")
def read_fd(fd: int64, n: int64) -> Own[bytes]: ...


@native("tpy::stdlib::os::write_fd")
def write_fd(fd: int64, data: bytes) -> int64: ...


@native("tpy::stdlib::os::lseek_fd")
def lseek_fd(fd: int64, pos: int64, how: int64) -> int64: ...


@native("tpy::stdlib::os::pipe_fd")
def pipe_fd() -> tuple[int64, int64]: ...


@native("tpy::stdlib::os::dup_fd")
def dup_fd(fd: int64) -> int64: ...


@native("tpy::stdlib::os::dup2_fd")
def dup2_fd(fd: int64, fd2: int64) -> int64: ...


@native("tpy::stdlib::os::fstat_fd")
def fstat_fd(fd: int64) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


# Metadata mutation + randomness.
@native("tpy::stdlib::os::chmod_path")
def chmod_path(path: str, mode: int64) -> None: ...


@native("tpy::stdlib::os::chown_path")
def chown_path(path: str, uid: int64, gid: int64) -> None: ...


@native("tpy::stdlib::os::utime_path")
def utime_path(path: str, atime: float, mtime: float) -> None: ...


@native("tpy::stdlib::os::access_path")
def access_path(path: str, mode: int64) -> bool: ...


@native("tpy::stdlib::os::urandom")
def urandom(n: int64) -> Own[bytes]: ...


# Process identity + small system queries.
@native("tpy::stdlib::os::getpid")
def getpid() -> int64: ...


@native("tpy::stdlib::os::getppid")
def getppid() -> int64: ...


@native("tpy::stdlib::os::getuid")
def getuid() -> int64: ...


@native("tpy::stdlib::os::geteuid")
def geteuid() -> int64: ...


@native("tpy::stdlib::os::getgid")
def getgid() -> int64: ...


@native("tpy::stdlib::os::getegid")
def getegid() -> int64: ...


@native("tpy::stdlib::os::getlogin")
def getlogin() -> str: ...


@native("tpy::stdlib::os::umask")
def umask(mask: int64) -> int64: ...


@native("tpy::stdlib::os::cpu_count")
def cpu_count_raw() -> int64: ...


@native("tpy::stdlib::os::strerror")
def strerror(code: int64) -> str: ...


@native("tpy::stdlib::os::isatty")
def isatty(fd: int64) -> bool: ...


# Hardlink / truncation / durability.
@native("tpy::stdlib::os::link_path")
def link_path(src: str, dst: str) -> None: ...


@native("tpy::stdlib::os::truncate_path")
def truncate_path(path: str, length: int64) -> None: ...


@native("tpy::stdlib::os::ftruncate_fd")
def ftruncate_fd(fd: int64, length: int64) -> None: ...


@native("tpy::stdlib::os::fsync_fd")
def fsync_fd(fd: int64) -> None: ...


@native("tpy::stdlib::os::terminal_size_raw")
def terminal_size_raw(fd: int64) -> tuple[int64, int64]: ...


# os.path predicates. The C++ helpers carry a `path_` prefix (to namespace
# them against the os-level calls in tpy::stdlib::os); the @native symbol
# pins that, so the TPy stubs keep the public os.path names.
@native("tpy::stdlib::os::path_exists")
def exists(path: str) -> bool: ...


@native("tpy::stdlib::os::path_lexists")
def lexists(path: str) -> bool: ...


@native("tpy::stdlib::os::path_isfile")
def isfile(path: str) -> bool: ...


@native("tpy::stdlib::os::path_isdir")
def isdir(path: str) -> bool: ...


@native("tpy::stdlib::os::path_islink")
def islink(path: str) -> bool: ...


@native("tpy::stdlib::os::path_getsize")
def getsize(path: str) -> int64: ...


@native("tpy::stdlib::os::path_realpath")
def realpath(path: str, strict: bool = False) -> str: ...

