# A literal sub-pattern on a union-typed field. A union carrying None still has
# two or more non-None members, so the field renders as a std::variant and the
# flat `field == <literal>` comparison would not compile. CPython does match the
# arm, so the diagnostic names an unimplemented comparison, not a type error.


class P:
    x: int | str | None

    def __init__(self, x: int | str | None) -> None:
        self.x = x


def describe(p: P) -> str:
    match p:
        case P(x=3):  # tpyc: error(/literal pattern not yet implemented for union field 'x'/)
            return "three"
        case _:
            return "other"


def main() -> None:
    pass


main()
