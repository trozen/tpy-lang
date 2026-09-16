# The mirror of opt_slot_decl_with_rebind: a container literal is admitted as
# the FIRST binding of a pointer-repr `Optional[container]` local, but not as a
# later REBIND -- the rebind writes through the slot, and a literal there has
# no row at the shared rvalue-reseat shape.
from tpy import int32, Own


class F:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def make(self) -> Own[list[int32]]:
        return [self.n, self.n]


def go(f: F) -> int32:
    a: list[int32] | None = f.make()
    a = [1, 2, 3]  # tpyc: error(/not yet supported/)
    if a is None:
        return 0
    return len(a)


def main() -> None:
    print(go(F(3)))


main()
