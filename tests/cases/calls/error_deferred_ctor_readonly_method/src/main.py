# A @readonly method's fields are readonly (docs/READONLY_DESIGN.md): a
# deferred construction passing one to a mutable parameter is refused.
from tpy import copy, int32, readonly


class Sink:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Wipe[T]:
    items: list[T]

    def __init__(self, xs: list[Sink]) -> None:
        xs.clear()
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


class C:
    xs: list[Sink]

    def __init__(self) -> None:
        self.xs = []

    @readonly
    def m(self) -> None:
        w = Wipe(self.xs)  # tpyc: error(/Cannot pass readonly\[list\[Sink\]\] as mutable list\[Sink\] in argument 'xs'/)
        w.put(1)


def main() -> None:
    C().m()


main()
