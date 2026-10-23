# A comprehension walrus target read after the comprehension must be bound
# before it: the comprehension may run zero times (CPython: UnboundLocalError).
from tpy import int32


def f(n: int32) -> None:
    zs = [x for x in range(n) if (y := x * 2 + 7) > 0]
    # The subject: `y` is bound only if `range(n)` yielded.
    print(len(zs), y)  # tpyc: error(/variable 'y' may not be assigned at this point/)


f(0)
