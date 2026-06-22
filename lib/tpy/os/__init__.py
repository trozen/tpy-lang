# os -- miscellaneous operating system interfaces.
# tpy: cpp_namespace("tpystd::os")
# tpy: include("<tpy/stdlib/os.hpp>")
#
# Filesystem queries over std::filesystem (mutating ops over raw POSIX
# syscalls); the path-string surface lives in the `os.path` submodule. Raw
# bindings live in `os._native`.
from typing import Final, overload, Iterator
from tpy import Int64, Own
from tpy.extern import native_global
from . import path
from .path import join as _join
from ._native import (
    getcwd, chdir, rmdir, remove, rename, symlink, readlink,
    listdir as _listdir, scandir_raw as _scandir_raw,
    mkdir as _mkdir, stat_raw as _stat_raw, lstat_raw as _lstat_raw,
    setenv as _setenv, unsetenv as _unsetenv,
    open_fd as _open_fd, close_fd as _close_fd, read_fd as _read_fd,
    write_fd as _write_fd, lseek_fd as _lseek_fd, pipe_fd as _pipe_fd,
    dup_fd as _dup_fd, dup2_fd as _dup2_fd, fstat_fd as _fstat_fd,
    getpid, getppid, getuid, geteuid, getgid, getegid, getlogin, umask,
    strerror, isatty, cpu_count_raw as _cpu_count_raw,
    link_path as _link_path, truncate_path as _truncate_path,
    ftruncate_fd as _ftruncate_fd, fsync_fd as _fsync_fd,
    terminal_size_raw as _terminal_size_raw,
    chmod_path as _chmod_path, chown_path as _chown_path,
    utime_path as _utime_path, access_path as _access_path,
    urandom as _urandom,
)
from ._environ import environ


# os.stat / lstat result. Field names match CPython so user code (and the cpy
# test phase, which sees the real os.stat_result) is portable. Native code
# returns a flat tuple (the @native helper cannot construct this TPy type); the
# wrapper unpacks it here. Attribute access only -- unlike CPython's structseq,
# this does not support the sequence protocol (st[0], len, iteration); those are
# a clean compile error, not a silent divergence.
class stat_result:
    def __init__(self, mode: Int64, ino: Int64, dev: Int64, nlink: Int64,
                 uid: Int64, gid: Int64, size: Int64,
                 atime: float, mtime: float, ctime: float,
                 atime_ns: Int64, mtime_ns: Int64, ctime_ns: Int64) -> None:
        self.st_mode = mode
        self.st_ino = ino
        self.st_dev = dev
        self.st_nlink = nlink
        self.st_uid = uid
        self.st_gid = gid
        self.st_size = size
        self.st_atime = atime
        self.st_mtime = mtime
        self.st_ctime = ctime
        self.st_atime_ns = atime_ns
        self.st_mtime_ns = mtime_ns
        self.st_ctime_ns = ctime_ns


def _wrap_stat(t: tuple[Int64, Int64, Int64, Int64, Int64, Int64, Int64,
                        float, float, float, Int64, Int64, Int64]
               ) -> Own[stat_result]:
    return stat_result(t[0], t[1], t[2], t[3], t[4], t[5], t[6],
                       t[7], t[8], t[9], t[10], t[11], t[12])


def stat(path: str) -> Own[stat_result]:
    return _wrap_stat(_stat_raw(path))


def lstat(path: str) -> Own[stat_result]:
    return _wrap_stat(_lstat_raw(path))


# st_mode S_IF* type bits, for DirEntry's stat fallback when readdir's d_type is
# unknown or a symlink (which is_dir/is_file must follow).
_S_IFMT: Final[Int64] = 0o170000
_S_IFDIR: Final[Int64] = 0o040000
_S_IFREG: Final[Int64] = 0o100000
_S_IFLNK: Final[Int64] = 0o120000


# os.scandir entry. `_kind` is the normalized readdir d_type (1 dir / 2 file /
# 3 symlink / 0 unknown). is_dir/is_file follow symlinks (CPython default), so a
# symlink or an unknown kind falls back to stat; is_symlink uses lstat. stat()
# returns a fresh stat_result each call (CPython caches per follow_symlinks).
class DirEntry:
    name: str
    path: str
    _kind: Int64

    def __init__(self, name: str, path: str, kind: Int64) -> None:
        self.name = name
        self.path = path
        self._kind = kind

    def is_dir(self) -> bool:
        if self._kind == 1:
            return True
        if self._kind == 2:
            return False
        return (_wrap_stat(_stat_raw(self.path)).st_mode & _S_IFMT) == _S_IFDIR

    def is_file(self) -> bool:
        if self._kind == 2:
            return True
        if self._kind == 1:
            return False
        return (_wrap_stat(_stat_raw(self.path)).st_mode & _S_IFMT) == _S_IFREG

    def is_symlink(self) -> bool:
        if self._kind == 3:
            return True
        if self._kind == 1 or self._kind == 2:
            return False
        return (_wrap_stat(_lstat_raw(self.path)).st_mode & _S_IFMT) == _S_IFLNK

    def stat(self) -> Own[stat_result]:
        return _wrap_stat(_stat_raw(self.path))


def scandir(path: str = ".") -> Own[list[DirEntry]]:
    out: list[DirEntry] = []
    for entry in _scandir_raw(path):
        out.append(DirEntry(entry[0], _join(path, entry[0]), entry[1]))
    return out


def listdir(path: str = ".") -> Own[list[str]]:
    return _listdir(path)


# Iterative (explicit-stack) directory tree walk. `yield from`/recursion are
# unsupported, so the descent is an explicit stack. In topdown mode the yielded
# `dirnames` aliases the frame, so the caller's in-place edit prunes the descent
# (the os.walk contract). A directory that can't be scanned is skipped, matching
# CPython's default `onerror=None`. The `onerror` callback and bottomup
# (topdown=False) are not yet supported.
def walk(top: str, topdown: bool = True,
         followlinks: bool = False) -> Iterator[tuple[str, list[str], list[str]]]:
    if not topdown:
        raise NotImplementedError(
            "os.walk(topdown=False) is not yet supported")
    stack: list[str] = []
    stack.append(top)
    while len(stack) > 0:
        cur = stack.pop()
        dirnames: list[str] = []
        filenames: list[str] = []
        try:
            entries = scandir(cur)
        except OSError:
            continue
        for e in entries:
            # A broken symlink (or otherwise unstattable entry) makes is_dir()
            # raise; CPython treats that as a non-directory, not an error.
            is_dir = False
            try:
                is_dir = e.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                dirnames.append(e.name)
            else:
                filenames.append(e.name)
        yield (cur, dirnames, filenames)
        # The caller may have pruned `dirnames` during the yield. Push the
        # survivors in reverse so they pop in `dirnames` order (CPython visits
        # subdirectories pre-order in scandir order).
        i = len(dirnames) - 1
        while i >= 0:
            child = _join(cur, dirnames[i])
            if followlinks or not path.islink(child):
                stack.append(child)
            i -= 1


def mkdir(path: str, mode: Int64 = 0o777) -> None:
    _mkdir(path, mode)


# Two overloads (mirroring typeshed): a non-None default narrows the result to
# `str`, so `len(os.getenv(k, ""))` type-checks. Both read the os.environ
# snapshot (CPython's getenv is environ.get), not libc -- so a bare os.putenv
# is not observable here, matching CPython.
@overload
def getenv(key: str) -> str | None:
    if key in environ:
        return environ[key]
    return None


@overload
def getenv(key: str, default: str) -> str:
    if key in environ:
        return environ[key]
    return default


# Low-level libc wrappers (CPython's os.putenv/os.unsetenv): they mutate the
# process environment directly and do NOT touch the os.environ snapshot, so a
# subsequent os.getenv/os.environ lookup will not see the change. Assign through
# os.environ[key] = value (or `del`) to keep the snapshot in sync.
def putenv(key: str, value: str) -> None:
    _setenv(key, value)


def unsetenv(key: str) -> None:
    _unsetenv(key)


# On POSIX os.replace is the same atomic rename(2) as os.rename (both overwrite
# an existing destination); the cross-platform guarantee is the only difference.
def replace(src: str, dst: str) -> None:
    rename(src, dst)


def makedirs(name: str, mode: Int64 = 0o777, exist_ok: bool = False) -> None:
    head = path.dirname(name)
    if len(head) > 0 and not path.exists(head):
        makedirs(head, mode, exist_ok)
    if exist_ok and path.isdir(name):
        return
    mkdir(name, mode)


# Remove `name`, then rmdir empty parents working upward, stopping at the
# first non-empty one (CPython removedirs). Walks via dirname -- each parent is
# a view narrowing into the live `head`, so the walk needs no owned copy.
def removedirs(name: str) -> None:
    rmdir(name)
    head = path.dirname(name)
    while len(head) > 0:
        try:
            rmdir(head)
        except OSError:
            break
        head = path.dirname(head)


# open()/lseek()/access() constants. These names (O_*, SEEK_*, *_OK) are libc
# macros present in the generated TU, so they cannot be emitted as C++ symbols;
# native_global binds each to a safe-named C++ global holding the real macro
# value (correct on every platform -- the O_CREAT family differs Linux/macOS).
O_RDONLY: Final[Int64] = native_global("tpy::stdlib::os::kc_o_rdonly")
O_WRONLY: Final[Int64] = native_global("tpy::stdlib::os::kc_o_wronly")
O_RDWR: Final[Int64] = native_global("tpy::stdlib::os::kc_o_rdwr")
O_CREAT: Final[Int64] = native_global("tpy::stdlib::os::kc_o_creat")
O_EXCL: Final[Int64] = native_global("tpy::stdlib::os::kc_o_excl")
O_TRUNC: Final[Int64] = native_global("tpy::stdlib::os::kc_o_trunc")
O_APPEND: Final[Int64] = native_global("tpy::stdlib::os::kc_o_append")
SEEK_SET: Final[Int64] = native_global("tpy::stdlib::os::kc_seek_set")
SEEK_CUR: Final[Int64] = native_global("tpy::stdlib::os::kc_seek_cur")
SEEK_END: Final[Int64] = native_global("tpy::stdlib::os::kc_seek_end")
F_OK: Final[Int64] = native_global("tpy::stdlib::os::kc_f_ok")
R_OK: Final[Int64] = native_global("tpy::stdlib::os::kc_r_ok")
W_OK: Final[Int64] = native_global("tpy::stdlib::os::kc_w_ok")
X_OK: Final[Int64] = native_global("tpy::stdlib::os::kc_x_ok")


def open(path: str, flags: Int64, mode: Int64 = 0o777) -> Int64:
    return _open_fd(path, flags, mode)


def close(fd: Int64) -> None:
    _close_fd(fd)


def read(fd: Int64, n: Int64) -> Own[bytes]:
    return _read_fd(fd, n)


def write(fd: Int64, data: bytes) -> Int64:
    return _write_fd(fd, data)


def lseek(fd: Int64, pos: Int64, how: Int64) -> Int64:
    return _lseek_fd(fd, pos, how)


def pipe() -> tuple[Int64, Int64]:
    return _pipe_fd()


def dup(fd: Int64) -> Int64:
    return _dup_fd(fd)


def dup2(fd: Int64, fd2: Int64) -> Int64:
    return _dup2_fd(fd, fd2)


def fstat(fd: Int64) -> Own[stat_result]:
    return _wrap_stat(_fstat_fd(fd))


def chmod(path: str, mode: Int64) -> None:
    _chmod_path(path, mode)


def chown(path: str, uid: Int64, gid: Int64) -> None:
    _chown_path(path, uid, gid)


# CPython's os.utime(path, times=(atime, mtime)). The no-arg (current time) and
# ns= forms take a required tuple here instead.
def utime(path: str, times: tuple[float, float]) -> None:
    _utime_path(path, times[0], times[1])


def access(path: str, mode: Int64) -> bool:
    return _access_path(path, mode)


def urandom(n: Int64) -> Own[bytes]:
    return _urandom(n)


def link(src: str, dst: str) -> None:
    _link_path(src, dst)


def truncate(path: str, length: Int64) -> None:
    _truncate_path(path, length)


def ftruncate(fd: Int64, length: Int64) -> None:
    _ftruncate_fd(fd, length)


def fsync(fd: Int64) -> None:
    _fsync_fd(fd)


# os.get_terminal_size result. Attribute access only (no tuple/sequence
# protocol, unlike CPython's terminal_size named tuple); st[0]/unpacking is a
# clean compile error, not a silent divergence.
class terminal_size:
    columns: Int64
    lines: Int64

    def __init__(self, columns: Int64, lines: Int64) -> None:
        self.columns = columns
        self.lines = lines


def get_terminal_size(fd: Int64 = 1) -> Own[terminal_size]:
    t = _terminal_size_raw(fd)
    return terminal_size(t[0], t[1])


# os.fspath on str is the identity; the PathLike form arrives with pathlib.
def fspath(path: str) -> str:
    return path


# CPython os.cpu_count() returns None when the count is indeterminate.
def cpu_count() -> Int64 | None:
    n = _cpu_count_raw()
    if n == 0:
        return None
    return n


def unlink(path: str) -> None:
    remove(path)


# POSIX path/line separators and special names (os.name is "posix"). altsep is
# None on POSIX (omitted; matches the os.path decision). These mirror the
# os.path constants for the values shared between the two modules.
name: Final[str] = "posix"
sep: Final[str] = "/"
extsep: Final[str] = "."
pathsep: Final[str] = ":"
linesep: Final[str] = "\n"
curdir: Final[str] = "."
pardir: Final[str] = ".."
devnull: Final[str] = "/dev/null"

