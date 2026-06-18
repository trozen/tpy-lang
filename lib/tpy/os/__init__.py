# os -- miscellaneous operating system interfaces.
# tpy: cpp_namespace("tpystd::os")
# tpy: include("<tpy/stdlib/os.hpp>")
#
# Filesystem queries over std::filesystem (mutating ops over raw POSIX
# syscalls); the path-string surface lives in the `os.path` submodule. Raw
# bindings live in `os._native`.
from typing import overload
from tpy import Int64, Own
from . import path
from ._native import (
    getcwd, chdir, listdir, rmdir, remove, rename, symlink, readlink,
    mkdir as _mkdir, stat_raw as _stat_raw, lstat_raw as _lstat_raw,
    env_has as _env_has, env_get as _env_get,
)


# os.stat / lstat result. Field names match CPython so user code (and the cpy
# test phase, which sees the real os.stat_result) is portable. Native code
# returns a flat tuple (the @native helper cannot construct this TPy type); the
# wrapper unpacks it here. Attribute access only -- unlike CPython's structseq,
# this does not support the sequence protocol (st[0], len, iteration); those are
# a clean compile error, not a silent divergence. st_blksize/st_blocks/st_rdev
# and the sequence protocol are deferred (see TODO.md).
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


def mkdir(path: str, mode: Int64 = 0o777) -> None:
    _mkdir(path, mode)


# Two overloads (mirroring typeshed): a non-None default narrows the result to
# `str`, so `len(os.getenv(k, ""))` type-checks. env_has + env_get is two libc
# lookups -- a single value-or-null lookup is a deferred cleanup (see TODO.md).
@overload
def getenv(key: str) -> str | None:
    if _env_has(key):
        return _env_get(key)
    return None


@overload
def getenv(key: str, default: str) -> str:
    if _env_has(key):
        return _env_get(key)
    return default


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
# first non-empty one (CPython removedirs). Walks parents via dirname (a view
# narrowing into `name`) rather than `split` tuple-unpack, which would dangle:
# see BUGS.md (tuple[str,str]-return unpacked into view locals).
def removedirs(name: str) -> None:
    rmdir(name)
    head = path.dirname(name)
    while len(head) > 0:
        try:
            rmdir(head)
        except OSError:
            break
        head = path.dirname(head)
