# `own_iter(x)` of a local bound to existing storage has no list of its own to
# give: refused, naming the binding (BUGS.md#own-iter-non-owning-source).
from tpy import int32, own_iter


def overwrite(w: list[int32]) -> int32:
    b = w
    print(len(b))
    b = [2, 3]
    total = 0
    for x in own_iter(b):  # tpyc: error(/own_iter\(\) takes ownership of 'b', which does not own its list \('b' is bound to existing storage at line 7\); iterate it with a plain for-loop instead/)
        total += x
    return total


print(overwrite([7]))
