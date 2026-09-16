# A pointer-repr `Optional[T]` local whose first binding is an optional FIELD
# read off a dying temporary materializes the whole optional into a slot, so the
# pointer is null whenever that field was None. A later rebind is refused: sema's
# alias-rebind pass answers the in-place question from the rebind's own rvalue
# type, stamps IN_PLACE, and the reseat would then write `(*p) = Point(..)`
# through the null pointer (BUGS.md#opt-storage-field-rebind-rejected).
from tpy import int32, Own


class Point:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag


class Holder:
    value: Point | None

    def __init__(self, x: int32) -> None:
        self.value = None
        if x > 0:
            self.value = Point("live")


def make_holder(x: int32) -> Own[Holder]:
    return Holder(x)


def risky(x: int32) -> str:
    p: Point | None = make_holder(x).value  # tpyc: error(/not yet supported/)
    p = Point("set")
    return p.tag


def main() -> None:
    print(risky(3))


main()
