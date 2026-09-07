# The RECORD payload of warn_return_container_borrow_dunder: a user `__add__`
# that returns a BORROW aliases an operand, so an `Own[Acc]` return slot copies
# it and warns. Sema reads the dunder through the same provenance rule the
# method-call spelling takes -- the dunder borrows an ARGUMENT (its operand),
# which no temporary receiver can bound. The copy is the ACKNOWLEDGED CPython
# divergence, so the case prints only what both agree on.
from tpy import Int32, Own, copy


class Acc:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __add__(self, o: 'Acc') -> 'Acc':
        return self if self.n >= o.n else o


def bigger(a: Acc, b: Acc) -> Own[Acc]:
    return a + b  # tpyc: warning(/copies Acc into owned storage/)


def bigger_copy(a: Acc, b: Acc) -> Own[Acc]:
    return copy(a + b)  # tpyc: ok


def main() -> None:
    print(bigger(Acc(3), Acc(1)).n, bigger_copy(Acc(3), Acc(1)).n)


main()
