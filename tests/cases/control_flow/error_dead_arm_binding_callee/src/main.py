# A dead arm's binding of a function's name makes it a local: the call reaches
# that unassigned local (CPython: UnboundLocalError), never the module function.
from tpy import int32


def g() -> int32:
    return 5


def f() -> None:
    if False:
        g = 1
    print(g())  # tpyc: error(/variable 'g' may not be assigned at this point/)


f()
