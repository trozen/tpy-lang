# A `t = items.pop()` declaration whose element is a pointer-variant union:
# the pop is admitted, the declaration slot is not.
from tpy import Int32


class A:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def f(items: list[A | str]) -> None:
    # The declaration's source is a ptr-variant union rvalue.
    t = items.pop()  # tpyc: error(/stmt\.var_decl:decl\.ptr_union_source/)
    if isinstance(t, A):
        print(t.n)


def main() -> None:
    f([A(1)])


main()
