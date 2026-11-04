# A nested def's read of an enclosing local that only a dead arm binds is
# that local's cell (CPython: NameError on the call), never the module global.
from tpy import int32

g = 5


def f() -> None:
    if False:
        g = 3

    def inner() -> int32:
        return g  # tpyc: error(/variable 'g' may not be assigned at this point/)

    print(inner())


f()
