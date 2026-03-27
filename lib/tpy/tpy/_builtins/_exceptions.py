# tpy: cpp_namespace("tpystd::builtins")
from .._bootstrap._extern import native
from .._core._types import ControlFlow


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp)
@native("tpy::BaseException")
class BaseException: ...

@native("tpy::Exception")
class Exception(BaseException): ...

@native("tpy::StopIteration")
class StopIteration(Exception, ControlFlow): ...
