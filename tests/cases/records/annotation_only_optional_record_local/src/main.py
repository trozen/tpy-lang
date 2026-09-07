# An annotation-only local of a non-value Optional record type: the slot is
# declared without an initializer, then assigned and narrowed before use.
from tpy import Int32


class R:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def build(x: Int32) -> Int32:
    r: R | None  # declared with no initializer -- a non-value no-init slot
    r = R(x)
    if r is not None:
        r.x = r.x + 1  # the record is aliased, not copied
        return r.x
    return 0


def main() -> None:
    print(build(4))


main()
