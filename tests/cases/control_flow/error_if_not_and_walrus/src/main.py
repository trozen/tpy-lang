# The `if` twin of the nested-short-circuit hole: `not (a and (b := ...))` is
# true whenever the `and` is false, and the `and` is false as soon as `a` is --
# with the walrus never evaluated. CPython raises UnboundLocalError here.
from tpy import int32


def g(x: int32) -> int32:
    return x - 1


def probe(k: int32) -> None:
    if not (k > 0 and (b := g(k)) >= 0):
        print("then", b)  # tpyc: error(/may not be assigned/)


def main() -> None:
    probe(0)


main()
