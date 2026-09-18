# An exposed iterator must end with StopIteration: CPython's iterator protocol
# has no other end signal, and a user return-only exception is a plain value
# the tp_iternext wrapper cannot turn into a Python error.
# tpy: ext_module
from tpy import int32, error_return, ReturnException
from tpy.extern import export


class Done(Exception, ReturnException):
    pass


@export
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> "Counter":
        return self

    # the subject: a plain TPy class may use its own end signal here
    @error_return(Done)
    def __next__(self) -> int32:  # tpyc: error(/'__next__' must end iteration with StopIteration, not 'Done'/)
        if self.n == 0:
            raise Done
        self.n -= 1
        return self.n
