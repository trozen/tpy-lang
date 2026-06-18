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


@native("tpy::stdlib::os::env_has")
def env_has(key: str) -> bool: ...


@native("tpy::stdlib::os::env_get")
def env_get(key: str) -> str: ...


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
