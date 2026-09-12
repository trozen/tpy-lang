# Mixed Optional[T] + Ptr[U] tuple field: storage form is
# std::tuple<std::optional<T>, U*>; the runtime tuple_to_storage helper
# must leave the Ptr slot alone instead of lifting every T*-shaped slot
# to std::optional. Regression for the
# "tuple_to_storage / tuple_to_pointer overload mis-converts Ptr[T]
# mixed with Optional[T]" bug.
from tpy import int32, Ptr


class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class Tag:
    name: int32
    def __init__(self, name: int32) -> None:
        self.name = name


class Holder:
    pair: tuple[Point | None, Ptr[Tag]]

    def __init__(self, pair: tuple[Point | None, Ptr[Tag]]) -> None:
        self.pair = pair  # tpyc: warning(/copies/)


def main() -> None:
    p = Point(1)
    tg = Tag(7)
    ptr: Ptr[Tag] = tg
    h1 = Holder((p, ptr))
    h2 = Holder((None, ptr))
    print(h1.pair[1].name)
    print(h2.pair[1].name)
    tg.name = 99
    print(h1.pair[1].name)


main()
