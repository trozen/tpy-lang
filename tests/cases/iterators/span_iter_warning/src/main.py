# Test that types with both __span__ and __iter__ compile without warning.
# __iter__ takes precedence for iteration in codegen.
from tpy import Int32, Own, Span, readonly_alt

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
    @readonly_alt
    def __span__(self) -> Span[readonly_alt[Int32]]:
        return self._data
    def __iter__(self) -> Own[MyIter]:
        return MyIter(Int32(3))

def main() -> None:
    d = Dual()
    for x in d:
        print(x)

main()
