# A head the value ranges prove EMPTY does not prove the body ran -- the same
# predicate that accepts `range(2)` answers `range(0)` with "may not run", so
# the read after the loop rejects. CPython runs the program and raises
# UnboundLocalError at that read.
from tpy import int32


def probe() -> int32:
    for i in range(0):
        w = i + 1
    return w  # tpyc: error(/variable 'w' may not be assigned at this point/)


def main() -> None:
    print(probe())


main()
