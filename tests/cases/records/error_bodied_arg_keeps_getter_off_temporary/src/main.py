# A bodied callee whose result borrows its argument keeps it: a getter read off a
# temporary is refused there (in-statement read: BUGS.md#borrowed-result-field-read-off-temporary-rejects).
from tpy import Own, int32


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class H:
    r: Rec

    def __init__(self) -> None:
        self.r = Rec(5)

    @property
    def rec(self) -> Rec:
        return self.r


def mk() -> Own[H]:
    return H()


def keep(r: Rec) -> Rec:
    return r


def main() -> None:
    # `r` borrows the temporary's record, which dies at the end of the line.
    r = keep(mk().rec)  # tpyc: warning(/Result borrows from temporary argument/) error(/arg\.lends_from_temporary/)
    print(r.x)


main()
