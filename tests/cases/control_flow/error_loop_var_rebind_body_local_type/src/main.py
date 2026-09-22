# A name two sibling loop bodies bind is one int32 local; a later loop head
# over it with `int` elements would write a second type into that one local.
from tpy import int32


def f(k: int32, end: int) -> int:
    total = 0
    for a in range(1, 3):
        n = k * 10 + a
        total += n
    for b in range(1, 3):
        n = k * 10 + b
        total += n
    # the head binds the local the two bodies declared
    for n in range(2, end):  # tpyc: error(/for-loop rebinds existing variable 'n' of type 'int32'/)
        total += n
    return total + n


print(f(3, 5))
