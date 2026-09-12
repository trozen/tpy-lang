# The adjacent shape the `Own[container] | None` return slot keeps rejecting: a
# container LITERAL source. The slot's rows take a None literal, an owning call
# rvalue and a borrow pointer local; a literal would need a typed-brace render
# the slot does not spell.
from tpy import int32, Own


def opt(flag: bool) -> Own[list[int32]] | None:
    if flag:
        return [1, 2]  # tpyc: error(/not yet supported/)
    return None


def main() -> None:
    x = opt(True)
    if x is not None:
        print(len(x))


main()
