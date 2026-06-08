# Sync[T] on a statically non-Sync type (mutable container) is a
# resolve-time error.
from tpy import Int32, Sync

def g(xs: Sync[list[Int32]]) -> None:  # tpyc: error(/'list\[Int32\]' is not Sync/)
    pass
