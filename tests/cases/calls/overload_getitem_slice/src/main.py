# User-type slicing via @overload __getitem__(slice).
# Tests slice type in unions, isinstance dispatch, and operator[] codegen.
from typing import overload
from tpy import Int32, Span, readonly

class MyList:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [Int32(10), Int32(20), Int32(30), Int32(40), Int32(50)]

    @overload
    def __getitem__(self, index: Int32) -> Int32: ...  # tpyc: ok

    @overload
    def __getitem__(self, index: slice) -> Span[readonly[Int32]]: ...  # tpyc: ok

    def __getitem__(self, index: Int32 | slice) -> Int32 | Span[readonly[Int32]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: Int32 = s_start if s_start is not None else Int32(0)
            stop: Int32 = s_stop if s_stop is not None else Int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]

def main() -> None:
    m = MyList()

    # Index access
    print(m[Int32(0)])
    print(m[Int32(3)])

    # Slice access with both bounds
    sp = m[Int32(1):Int32(4)]
    for x in sp:
        print(x)

    # Slice with omitted start
    sp2 = m[:Int32(2)]
    for x in sp2:
        print(x)

    # Slice with omitted stop
    sp3 = m[Int32(3):]
    for x in sp3:
        print(x)

    # Slice with both omitted
    sp4 = m[:]
    for x in sp4:
        print(x)

main()
