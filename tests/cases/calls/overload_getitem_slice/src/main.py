# User-type slicing via @overload __getitem__(slice).
# Tests slice type in unions, isinstance dispatch, and operator[] codegen.
from typing import overload
from tpy import int32, Span, readonly

class MyList:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [int32(10), int32(20), int32(30), int32(40), int32(50)]

    @overload
    def __getitem__(self, index: int32) -> int32: ...  # tpyc: ok

    @overload
    def __getitem__(self, index: slice) -> Span[readonly[int32]]: ...  # tpyc: ok

    def __getitem__(self, index: int32 | slice) -> int32 | Span[readonly[int32]]:
        if isinstance(index, slice):
            s_start = index.start
            s_stop = index.stop
            start: int32 = s_start if s_start is not None else int32(0)
            stop: int32 = s_stop if s_stop is not None else int32(len(self._data))
            return self._data[start:stop]
        else:
            return self._data[index]

def main() -> None:
    m = MyList()

    # Index access
    print(m[int32(0)])
    print(m[int32(3)])

    # Slice access with both bounds
    sp = m[int32(1):int32(4)]
    for x in sp:
        print(x)

    # Slice with omitted start
    sp2 = m[:int32(2)]
    for x in sp2:
        print(x)

    # Slice with omitted stop
    sp3 = m[int32(3):]
    for x in sp3:
        print(x)

    # Slice with both omitted
    sp4 = m[:]
    for x in sp4:
        print(x)

main()
