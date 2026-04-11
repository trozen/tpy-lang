# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._bootstrap._extern import native
from .._core._types import ReturnException, StrView


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp)
@native("tpy::BaseException")
class BaseException:
    message: str

    @native("tpy::BaseException", function=True)
    def __init__(self, message: str = "") -> None: ...

    def __str__(self) -> StrView: ...

@native("tpy::Exception")
class Exception(BaseException):
    @native("tpy::Exception", function=True)
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ValueError")
class ValueError(Exception):
    @native("tpy::ValueError", function=True)
    def __init__(self, message: str = "") -> None: ...

@native("tpy::OSError")
class OSError(Exception):
    @native("tpy::OSError", function=True)
    def __init__(self, message: str = "") -> None: ...

@native("tpy::FileNotFoundError")
class FileNotFoundError(OSError):
    @native("tpy::FileNotFoundError", function=True)
    def __init__(self, message: str = "") -> None: ...

@native("tpy::StopIteration")
class StopIteration(Exception, ReturnException): ...
