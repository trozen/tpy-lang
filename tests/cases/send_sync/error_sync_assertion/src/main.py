# Sync[T] on a statically non-Sync type (mutable container) is a
# resolve-time error.
from tpy import int32, Sync

def g(xs: Sync[list[int32]]) -> None:  # tpyc: error(/'list\[int32\]' is not Sync/)
    pass
