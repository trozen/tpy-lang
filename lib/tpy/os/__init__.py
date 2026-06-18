# os -- miscellaneous operating system interfaces.
# tpy: cpp_namespace("tpystd::os")
# tpy: include("<tpy/stdlib/os.hpp>")
#
# Filesystem queries over std::filesystem; the path-string surface lives in
# the `os.path` submodule. Raw bindings live in `os._native`.
from . import path
from ._native import getcwd, chdir, listdir, env_has as _env_has, env_get as _env_get


def getenv(key: str, default: str | None = None) -> str | None:
    if _env_has(key):
        return _env_get(key)
    return default
