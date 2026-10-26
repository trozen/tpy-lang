# A return exception carries only the fields it declares (ERROR_RETURN_DESIGN.md
# "Exception Type"), so `StopIteration("message")` is refused; CPython accepts it.
from __future__ import annotations
from tpy import int32

class Iter:
    def __iter__(self) -> Iter:
        return self

    def __next__(self) -> int32:
        raise StopIteration("message")  # tpyc: error(/'StopIteration' has no __init__: a return-only exception carries only the fields it declares$/)
