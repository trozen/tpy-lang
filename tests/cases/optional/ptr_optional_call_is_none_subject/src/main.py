# An `Optional[record]`-returning call used directly as an `is None`
# subject: the borrowed pointer result is compared bare, and the same
# optional-record name passes straight back out of a returning slot.
from tpy import Int32


class Rec:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def find(xs: list[Rec], want: Int32) -> Rec | None:
    for r in xs:
        if r.v == want:
            return r
    return None


def passthrough(x: Rec | None) -> Rec | None:
    # A bare optional-record name returned at the same optional slot.
    return x


def main() -> None:
    xs = [Rec(1)]
    # The call result itself is what the None test compares.
    print(find(xs, 1) is None)
    print(find(xs, 2) is None)
    print(passthrough(find(xs, 1)) is None)
    # The returned optional BORROWS the list element, so a write through it
    # shows through the original owner.
    found = find(xs, 1)
    if found is not None:
        found.v = 9
    print(xs[0].v)


main()
