# A @dispatch set of __getitem__ variants gets one operator[] per index type,
# like @overload stubs do; the element reference it returns aliases the store.
from tpy import dispatch, Int32, Own, Span, readonly, auto_readonly


class Cell:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


# generic record, @auto_readonly variants: mutable + const operator per index type
class Store[T]:
    _data: list[T]

    def __init__(self) -> None:
        self._data = []

    def add(self, item: Own[T]) -> None:
        self._data.append(item)

    @dispatch
    @auto_readonly
    def __getitem__(self, index: Int32) -> T:  # tpyc: ok
        return self._data[index]

    @dispatch
    @auto_readonly
    def __getitem__(self, index: slice) -> Span[auto_readonly[T]]:
        s_start = index.start
        s_stop = index.stop
        start: Int32 = s_start if s_start is not None else Int32(0)
        stop: Int32 = s_stop if s_stop is not None else Int32(len(self._data))
        return self._data[start:stop]


# non-generic record, @readonly variants: a const operator per index type
class Digits:
    xs: list[Int32]

    def __init__(self) -> None:
        self.xs = [4, 5, 6]

    @dispatch
    @readonly
    def __getitem__(self, index: Int32) -> Int32:  # tpyc: ok
        return self.xs[index]

    @dispatch
    @readonly
    def __getitem__(self, index: slice) -> Span[readonly[Int32]]:
        s_start = index.start
        s_stop = index.stop
        start: Int32 = s_start if s_start is not None else Int32(0)
        stop: Int32 = s_stop if s_stop is not None else Int32(len(self.xs))
        return self.xs[start:stop]


# readonly receiver: the const operator serves both index types
def read_store(s: readonly[Store[Int32]]) -> None:
    first = s[0]
    win = s[1:3]
    print("const:", first, win[0], win[1])


def main() -> None:
    s = Store[Cell]()
    s.add(Cell(1))
    s.add(Cell(2))
    s.add(Cell(3))
    # mutable receiver: the element comes back by reference, so the store sees the write
    s[0].v = 10
    win = s[1:3]
    win[0].v = 20
    print("mutable:", s[0].v, s[1].v, s[2].v)
    n = Store[Int32]()
    n.add(7)
    n.add(8)
    n.add(9)
    read_store(n)
    d = Digits()
    tail = d[1:3]
    print("scalar:", d[2], tail[0], len(tail))


main()
