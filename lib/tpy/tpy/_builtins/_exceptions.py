# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._bootstrap._extern import native
from .._core._types import ReturnException, StrView


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp).
# The class-level @native is sufficient for both `raise X("msg")` and
# `e = X("msg")` codegen paths, so __init__ stubs don't need their own
# @native(..., function=True) annotation.
@native("tpy::BaseException")
class BaseException:
    message: str

    def __init__(self, message: str = "") -> None: ...

    def __str__(self) -> StrView: ...

@native("tpy::Exception")
class Exception(BaseException):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ValueError")
class ValueError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::OSError")
class OSError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::FileNotFoundError")
class FileNotFoundError(OSError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::AttributeError")
class AttributeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::AssertionError")
class AssertionError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::IndexError")
class IndexError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::KeyError")
class KeyError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ArithmeticError")
class ArithmeticError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::ZeroDivisionError")
class ZeroDivisionError(ArithmeticError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::OverflowError")
class OverflowError(ArithmeticError):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::TypeError")
class TypeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::NotImplementedError")
class NotImplementedError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::RuntimeError")
class RuntimeError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::MemoryError")
class MemoryError(Exception):
    def __init__(self, message: str = "") -> None: ...

@native("tpy::StopIteration")
class StopIteration(Exception, ReturnException): ...
