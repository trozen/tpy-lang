# os._native -- raw @native bindings to the tpy::stdlib::os C++ helpers.
# A leaf module (imports nothing from the os package) so both os/__init__ and
# os/path can share these stubs without os.path -- which has module-level
# constants and so cannot participate in a cycle -- importing os/__init__.
# Internal: import via `os` / `os.path`, not directly.
# tpy: native_module
# tpy: cpp_namespace("tpystd::os")
# tpy: include("<tpy/stdlib/os.hpp>")
from tpy import Int64, Own
from tpy.extern import native


@native("tpy::stdlib::os::getcwd")
def getcwd() -> str: ...


@native("tpy::stdlib::os::chdir")
def chdir(path: str) -> None: ...


@native("tpy::stdlib::os::listdir")
def listdir(path: str) -> Own[list[str]]: ...


@native("tpy::stdlib::os::mkdir")
def mkdir(path: str, mode: Int64) -> None: ...


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
        Int64, Int64, Int64, Int64, Int64, Int64, Int64,
        float, float, float, Int64, Int64, Int64]: ...


@native("tpy::stdlib::os::lstat_raw")
def lstat_raw(path: str) -> tuple[
        Int64, Int64, Int64, Int64, Int64, Int64, Int64,
        float, float, float, Int64, Int64, Int64]: ...


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
def getsize(path: str) -> Int64: ...


@native("tpy::stdlib::os::path_realpath")
def realpath(path: str) -> str: ...
