# An @error_return property accessor can't cross the CPython boundary: the
# compiled getter returns a std::expected the getset wrapper has no unwrap
# step for (same rule as plain methods).
# tpy: ext_module
from tpy import int64, error_return, ReturnException
from tpy.extern import export


class Bad(Exception, ReturnException):
    pass


@export
class Box:
    _v: int64

    def __init__(self) -> None:
        self._v = 1

    @property
    @error_return(Bad)
    def val(self) -> int64:  # tpyc: error(/'val' cannot use @error_return/)
        return self._v
