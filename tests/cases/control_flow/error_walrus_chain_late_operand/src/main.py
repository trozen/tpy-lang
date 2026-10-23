# A walrus in a later operand of a chained comparison runs only when the
# comparisons before it held, so a read after the comparison may not be
# assigned.
from tpy import int32


def g(k: int32) -> int32:
    return k + 1


def f(k: int32) -> int32:
    v = 0 < k < (m := g(k))
    print(v)
    # The subject: `m` is bound only when `0 < k` held.
    return m  # tpyc: error(/variable 'm' may not be assigned at this point/)


print(f(-1))
