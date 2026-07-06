# An @error_return method can't cross the CPython boundary: it returns a
# std::expected the plain-method wrapper has no unwrap step for.
# tpy: ext_module
from tpy import Int64, error_return, ReturnException
from tpy.extern import export


class Bad(Exception, ReturnException):
    pass


@export
class C:
    def __init__(self, a: Int64):
        self.a = a

    @error_return(Bad)
    def half(self, x: Int64) -> Int64:  # tpyc: error(/'half' cannot use @error_return/)
        if x == 0:
            raise Bad
        return x // 2
