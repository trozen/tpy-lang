# T is inferred THROUGH the transparent Send[Own[T]] marker wrapper -- at the
# call site (fwd(Item(...))) and when forwarding the marker-typed param into a
# plain Own[T] param (the marker must not leak into the inferred type).
from tpy import Own, Send


class Item:
    v: int

    def __init__(self, v: int):
        self.v = v


def sink[T](item: Own[T]) -> Own[T]:
    return item


def fwd[T](item: Send[Own[T]]) -> Own[T]:
    return sink(item)   # tpyc: ok


def main() -> None:
    x = fwd(Item(7))    # tpyc: ok
    print(x.v)


main()
