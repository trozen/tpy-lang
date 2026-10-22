# An `except` target reusing a local's name is its handler's binding: storing
# it into owned storage is the slicing rejection the same handler gets without
# the earlier local (docs/LANGUAGE_FEATURES.md "Slicing-site sema rejection"),
# never a copy that drops the subclass.
from tpy import int32


class E(Exception):
    def __init__(self, x: int32) -> None:
        super().__init__("e")
        self.x = x


class F(E):
    def __init__(self, x: int32) -> None:
        super().__init__(x)


class S:
    errs: list[E]

    def __init__(self) -> None:
        self.errs = []


def f(s: S, k: int32) -> None:
    e = E(1)
    s.errs.append(e)
    try:
        raise F(k)
    except E as e:
        # the handler's `e` is the exception in flight, which may be an `F`
        s.errs.append(e)  # tpyc: error(/cannot store 'E' borrow as owned 'E' in argument 'value'.*slicing/)


def main() -> None:
    s = S()
    f(s, 5)
    print(len(s.errs))


main()
