# Reading a scalar field off an unproven Optional[record] tuple element:
# t[1].n where t[1] is `Leaf | None`. Codegen adds a runtime null check
# (deref_check); here the element is Some, so the check passes and the field
# reads through. Read-focused (the deref_check path), not an aliasing test.
from tpy import int32


class Leaf:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def first_n(t: tuple[int32, Leaf | None]) -> int32:
    return t[1].n  # tpyc: warning(/Potential None access/)


def main() -> None:
    leaf = Leaf(7)
    print(first_n((1, leaf)))


main()
