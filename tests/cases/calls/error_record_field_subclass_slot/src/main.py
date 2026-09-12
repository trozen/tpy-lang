# A field holding a SUBCLASS passed at a base-class parameter: the
# field-reference argument row admits the exact slot type only, so
# `base_use(h.c)` rejects.
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Child(A):
    def __init__(self) -> None:
        super().__init__(2)


class H2:
    c: Child

    def __init__(self) -> None:
        self.c = Child()


def base_use(a: A) -> int32:
    return a.x


def f() -> None:
    h = H2()
    # The field's static type is the subclass, the slot's is the base.
    print(base_use(h.c))  # tpyc: error(/stmt\.expr_stmt:call\.arg_shape\.record_f1/)


def main() -> None:
    f()


main()
