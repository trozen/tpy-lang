# tpy: ext_module
# User exception classes cross the @export boundary as their own Python type
# (PyErr_NewException at PyInit_), inheriting their built-in base -- catchable as
# the specific type AND the base, and importable as userexc.<Name>. Message-only
# exceptions (the message is the only state) cross faithfully. ConfigError tests
# a user base (chain ConfigError -> AppError -> Exception).
from tpy import int64
from tpy.extern import export


class NotFound(KeyError):
    def __init__(self, message: str):
        self.message = message


class AppError(Exception):
    def __init__(self, message: str):
        self.message = message


class ConfigError(AppError):
    def __init__(self, message: str):
        self.message = message


class FatalError(BaseException):  # direct BaseException base -> NOT an Exception
    def __init__(self, message: str):
        self.message = message


@export
def lookup(k: int64) -> int64:
    if k != 1:
        raise NotFound("no such key")
    return 100


@export
def boot(ok: int64) -> int64:
    if ok == 0:
        raise ConfigError("bad config")
    return 1


@export
def fail_app() -> int64:
    raise AppError("app failure")


@export
def abort_now() -> int64:
    raise FatalError("fatal")


@export
def must_be_even(n: int64) -> int64:
    # A built-in raised from a module that also defines user excs: set_py_err_from
    # misses the registry (no typeid match) and falls to the built-in cascade.
    if n % 2 != 0:
        raise ValueError("must be even")
    return n
