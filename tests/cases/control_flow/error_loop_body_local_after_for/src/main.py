# A local first bound in a for body is NOT assigned after a loop that may run
# zero times -- the read below is the reject CPython answers with
# UnboundLocalError. The pointer-alias flavor: before the rule landed this
# compiled to an uninitialized `Pic*` read.
from tpy import int32


class Pic:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def probe(ps: list[Pic]) -> int32:
    for p in ps:
        q = p
    return q.n  # tpyc: error(/variable 'q' may not be assigned at this point/)


def main() -> None:
    print(probe([]))


main()
