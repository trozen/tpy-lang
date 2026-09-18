# A walrus behind an `and` NESTED in an `or`: the head comes out true either
# through the `and` (which bound the target) or through the `or`'s right
# operand alone (which did not), so the body may not read it. CPython raises
# UnboundLocalError on the second path, which `probe(-1)` below takes.
from tpy import int32


def g(x: int32) -> int32:
    return x - 1


def probe(k: int32) -> None:
    while (k > 0 and (b := g(k)) >= 0) or k < 0:
        print("body", b)  # tpyc: error(/may not be assigned/)
        k += 1


def main() -> None:
    probe(-1)


main()
