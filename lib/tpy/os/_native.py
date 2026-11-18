# os._native -- raw @native bindings to the tpy::stdlib::os C++ helpers.
# A leaf module (imports nothing from the os package) so both os/__init__ and
# os/path can share these stubs without os.path -- which has module-level
# constants and so cannot participate in a cycle -- importing os/__init__.
# Internal: import via `os` / `os.path` (and `subprocess` for spawn_raw), not
# directly.
__tpy__ = {"mod": "native",
           "cpp_namespace": "tpystd::os",
           "includes": ["<tpy/stdlib/os.hpp>"]}
from tpy import int64, Own, pure
from tpy.extern import native


@native("tpy::stdlib::os::getcwd", transient=True)
def getcwd() -> str: ...


@native("tpy::stdlib::os::chdir", transient=True)
def chdir(path: str) -> None: ...


@native("tpy::stdlib::os::listdir", transient=True)
def listdir(path: str) -> Own[list[str]]: ...


# (name, kind) per entry; kind 0 unknown / 1 dir / 2 file / 3 symlink.
@native("tpy::stdlib::os::scandir_raw", transient=True)
def scandir_raw(path: str) -> Own[list[tuple[str, int64]]]: ...


@native("tpy::stdlib::os::mkdir", transient=True)
def mkdir(path: str, mode: int64) -> None: ...


@native("tpy::stdlib::os::rmdir", transient=True)
def rmdir(path: str) -> None: ...


@native("tpy::stdlib::os::remove", transient=True)
def remove(path: str) -> None: ...


@native("tpy::stdlib::os::rename", transient=True)
def rename(src: str, dst: str) -> None: ...


@native("tpy::stdlib::os::symlink", transient=True)
def symlink(target: str, linkpath: str) -> None: ...


@native("tpy::stdlib::os::readlink")
def readlink(path: str) -> str: ...


# 13-field stat tuple (see os/__init__ stat_result for the field order). The
# type is spelled inline -- a @native return annotation does not resolve a
# module-level type alias.
@native("tpy::stdlib::os::stat_raw", transient=True)
def stat_raw(path: str) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


@native("tpy::stdlib::os::lstat_raw", transient=True)
def lstat_raw(path: str) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


@native("tpy::stdlib::os::path_getmtime", transient=True)
def path_getmtime(path: str) -> float: ...


@native("tpy::stdlib::os::path_getatime", transient=True)
def path_getatime(path: str) -> float: ...


@native("tpy::stdlib::os::path_getctime", transient=True)
def path_getctime(path: str) -> float: ...


@native("tpy::stdlib::os::path_samefile", transient=True)
def path_samefile(a: str, b: str) -> bool: ...


@native("tpy::stdlib::os::path_ismount", transient=True)
def path_ismount(path: str) -> bool: ...


@native("tpy::stdlib::os::environ_keys", transient=True)
def environ_keys() -> Own[list[str]]: ...


@native("tpy::stdlib::os::env_get")
def env_get(key: str) -> str: ...


@native("tpy::stdlib::os::setenv", transient=True)
def setenv(key: str, value: str) -> None: ...


@native("tpy::stdlib::os::unsetenv", transient=True)
def unsetenv(key: str) -> None: ...


# pwd home-dir lookups for os.path.expanduser (empty str if not found).
@native("tpy::stdlib::os::current_home", transient=True)
def current_home() -> str: ...


@native("tpy::stdlib::os::user_home")
def user_home(name: str) -> str: ...


# Low-level fd I/O (raw POSIX; each raises OSError on failure).
@native("tpy::stdlib::os::open_fd", transient=True)
def open_fd(path: str, flags: int64, mode: int64) -> int64: ...


@native("tpy::stdlib::os::close_fd", transient=True)
def close_fd(fd: int64) -> None: ...


# The EINTR retry loops re-check signals (checks_signals), although SA_RESTART
# keeps them from ever seeing EINTR: a blocking read / write / waitpid sleeps
# through a Ctrl-C (BUGS.md#sigint-sa-restart-blocking-calls). An owner of a
# blocking fd waits through `_interrupt` first.
@native("tpy::stdlib::os::read_fd", checks_signals=True)
def read_fd(fd: int64, n: int64) -> Own[bytes]: ...


@native("tpy::stdlib::os::write_fd", checks_signals=True)
def write_fd(fd: int64, data: bytes) -> int64: ...


@native("tpy::stdlib::os::lseek_fd", transient=True)
def lseek_fd(fd: int64, pos: int64, how: int64) -> int64: ...


@native("tpy::stdlib::os::pipe_fd", transient=True)
def pipe_fd() -> tuple[int64, int64]: ...


@native("tpy::stdlib::os::get_blocking_fd", transient=True)
def get_blocking_fd(fd: int64) -> bool: ...


@native("tpy::stdlib::os::set_blocking_fd", transient=True)
def set_blocking_fd(fd: int64, blocking: bool) -> None: ...


@native("tpy::stdlib::os::openpty_fd", transient=True)
def openpty_fd() -> tuple[int64, int64]: ...


@native("tpy::stdlib::os::dup_fd", transient=True)
def dup_fd(fd: int64) -> int64: ...


@native("tpy::stdlib::os::dup2_fd", transient=True)
def dup2_fd(fd: int64, fd2: int64) -> int64: ...


@native("tpy::stdlib::os::fstat_fd", transient=True)
def fstat_fd(fd: int64) -> tuple[
        int64, int64, int64, int64, int64, int64, int64,
        float, float, float, int64, int64, int64]: ...


# Metadata mutation + randomness.
@native("tpy::stdlib::os::chmod_path", transient=True)
def chmod_path(path: str, mode: int64) -> None: ...


@native("tpy::stdlib::os::chown_path", transient=True)
def chown_path(path: str, uid: int64, gid: int64) -> None: ...


@native("tpy::stdlib::os::utime_path", transient=True)
def utime_path(path: str, atime: float, mtime: float) -> None: ...


@native("tpy::stdlib::os::access_path", transient=True)
def access_path(path: str, mode: int64) -> bool: ...


@native("tpy::stdlib::os::urandom", transient=True)
def urandom(n: int64) -> Own[bytes]: ...


# Process identity + small system queries.
@native("tpy::stdlib::os::getpid", transient=True)
def getpid() -> int64: ...


@native("tpy::stdlib::os::getppid", transient=True)
def getppid() -> int64: ...


@native("tpy::stdlib::os::getuid", transient=True)
def getuid() -> int64: ...


@native("tpy::stdlib::os::geteuid", transient=True)
def geteuid() -> int64: ...


@native("tpy::stdlib::os::getgid", transient=True)
def getgid() -> int64: ...


@native("tpy::stdlib::os::getegid", transient=True)
def getegid() -> int64: ...


@native("tpy::stdlib::os::getlogin", transient=True)
def getlogin() -> str: ...


@native("tpy::stdlib::os::umask", transient=True)
def umask(mask: int64) -> int64: ...


@native("tpy::stdlib::os::cpu_count", transient=True)
def cpu_count_raw() -> int64: ...


@native("tpy::stdlib::os::strerror", transient=True)
def strerror(code: int64) -> str: ...


@native("tpy::stdlib::os::isatty", transient=True)
def isatty(fd: int64) -> bool: ...


# Hardlink / truncation / durability.
@native("tpy::stdlib::os::link_path", transient=True)
def link_path(src: str, dst: str) -> None: ...


@native("tpy::stdlib::os::truncate_path", transient=True)
def truncate_path(path: str, length: int64) -> None: ...


@native("tpy::stdlib::os::ftruncate_fd", transient=True)
def ftruncate_fd(fd: int64, length: int64) -> None: ...


@native("tpy::stdlib::os::fsync_fd", transient=True)
def fsync_fd(fd: int64) -> None: ...


@native("tpy::stdlib::os::terminal_size_raw", transient=True)
def terminal_size_raw(fd: int64) -> tuple[int64, int64]: ...


# Process control.
@native("tpy::stdlib::os::kill_pid", checks_signals=True)
def kill_pid(pid: int64, sig: int64) -> None: ...


@native("tpy::stdlib::os::waitpid", checks_signals=True)
def waitpid(pid: int64, options: int64) -> tuple[int64, int64]: ...


# An fd that turns readable when process `pid` exits, or -1 where the OS has
# none (anything but Linux, an old kernel) or the pid is gone.
@native("tpy::stdlib::os::pidfd_open", transient=True)
def pidfd_open(pid: int64) -> int64: ...


@pure
@native("tpy::stdlib::os::waitstatus_to_exitcode")
def waitstatus_to_exitcode(status: int64) -> int64: ...


# subprocess.Popen's spawn: (pid, stdin write end, stdout read end, stderr
# read end), -1 for a stream without a pipe. Stream specs and inherit_mask are
# documented on the C++ declaration in os.hpp.
@native("tpy::stdlib::os::spawn_raw")
def spawn_raw(args: list[str], stdin_spec: int64,
              stdout_spec: int64, stderr_spec: int64, inherit_mask: int64
              ) -> tuple[int64, int64, int64, int64]: ...


# os.path predicates. The C++ helpers carry a `path_` prefix (to namespace
# them against the os-level calls in tpy::stdlib::os); the @native symbol
# pins that, so the TPy stubs keep the public os.path names.
@native("tpy::stdlib::os::path_exists", transient=True)
def exists(path: str) -> bool: ...


@native("tpy::stdlib::os::path_lexists", transient=True)
def lexists(path: str) -> bool: ...


@native("tpy::stdlib::os::path_isfile", transient=True)
def isfile(path: str) -> bool: ...


@native("tpy::stdlib::os::path_isdir", transient=True)
def isdir(path: str) -> bool: ...


@native("tpy::stdlib::os::path_islink", transient=True)
def islink(path: str) -> bool: ...


@native("tpy::stdlib::os::path_getsize", transient=True)
def getsize(path: str) -> int64: ...


@native("tpy::stdlib::os::path_realpath")
def realpath(path: str, strict: bool = False) -> str: ...

