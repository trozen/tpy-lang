# The ARGUMENT position of the same shape still rejects: an argument's
# admission runs on the argument registry, which does not read the use
# channel's form verdict, so the transient row that admits `if b.items_m():`
# does not reach `sink_l(b.items_m())`.
# See BUGS.md#borrow-container-ret-arg-rejects. Workaround: bind the result to
# a local first and pass the name.
from tpy import int32


class B:
    items: list[int32]

    def __init__(self, t: int32) -> None:
        self.items = [t]

    def items_m(self) -> list[int32]:
        return self.items


def sink_l(xs: list[int32]) -> int32:
    return len(xs)


def main() -> None:
    b = B(1)
    print(sink_l(b.items_m()))  # tpyc: error(/not yet supported/)


main()
