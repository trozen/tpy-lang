# C1 and M1 are bound in `rm` when this runs; C2, rc's main, M2 and total
# are not.
from rm import *  # tpyc: warning(/'rm' is still being imported .* before it defines 'C2', 'main', 'M2', 'total'; CPython's star import binds none of them here/)


def grand_total() -> int:
    return C1(1).v + M1(10).v + C2(2).v + M2(20).v
