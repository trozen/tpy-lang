# Test that a type with both __span__ and a non-SpanIter __iter__ compiles
# cleanly: for-loops use __iter__/__next__ (the universal default), and no
# begin/end is synthesized -- emitting __span__-based begin/end would
# silently diverge from the user's __iter__ ordering.
from tpy import Int32, Own, Span, auto_readonly

class MyIter:
    _val: Int32
    def __init__(self, val: Int32) -> None:
        self._val = val
    def __next__(self) -> Int32:
        if self._val > 0:
            result = self._val
            self._val -= 1
            return result
        raise StopIteration

class Dual:  # tpyc: ok
    _data: list[Int32]
    def __init__(self) -> None:
        self._data = [1, 2, 3]
    @auto_readonly
    def __span__(self) -> Span[auto_readonly[Int32]]:
        return self._data
    def __iter__(self) -> Own[MyIter]:
        return MyIter(Int32(3))

def main() -> None:
    d = Dual()
    for x in d:
        print(x)

main()
