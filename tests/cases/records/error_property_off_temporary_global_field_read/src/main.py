# A field read through a getter returning a GLOBAL's record, off a temporary, is
# refused as `x = mk().gch` is (BUGS.md#getter-global-off-temporary-rejects).
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Rec:
    sub: Box

    def __init__(self) -> None:
        self.sub = Box(5)


GREC: Rec = Rec()


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def gch(self) -> Rec:
        return GREC


def mk() -> Own[H]:
    return H()


def main() -> None:
    # A copy here would silently stop aliasing GREC (CPython prints 99).
    y = mk().gch.sub  # tpyc: error(/local_decl\.lends_from_temporary/)
    y.n = 99
    print(GREC.sub.n)


main()
