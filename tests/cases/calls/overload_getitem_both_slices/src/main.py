# User type with separate overloads for basic_slice and slice.
# Tests that a[1:3] dispatches to basic_slice and a[::2] dispatches to slice.
from typing import overload
from tpy import Int32, Span, readonly

class Window:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [10, 20, 30, 40, 50]

    @overload
    def __getitem__(self, index: basic_slice) -> Span[readonly[Int32]]: ...  # tpyc: ok

    @overload
    def __getitem__(self, index: slice) -> Span[readonly[Int32]]: ...  # tpyc: ok

    def __getitem__(self, index: basic_slice | slice) -> Span[readonly[Int32]]:
        if isinstance(index, basic_slice):
            # Basic: return contiguous subspan
            s_start = index.start
            s_stop = index.stop
            start: Int32 = s_start if s_start is not None else 0
            stop: Int32 = s_stop if s_stop is not None else len(self._data)
            return self._data[start:stop]
        else:
            # Stepped: access .step to prove we received a slice, not basic_slice
            s_step = index.step
            step: Int32 = s_step if s_step is not None else 1
            print(step)
            # Return full span (step logic deferred to caller for this test)
            return self._data[0:len(self._data)]

def main() -> None:
    w = Window()

    # basic_slice dispatch (a[1:4]) -- no step printed
    sp = w[1:4]
    for x in sp:
        print(x)

    # stepped slice dispatch (a[0:5:2]) -- prints step=2
    sp2 = w[0:5:2]

    # stepped slice dispatch (a[0:5:-1]) -- prints step=-1
    sp3 = w[0:5:-1]

main()
