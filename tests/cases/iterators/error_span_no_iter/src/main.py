# Test that iterating over a type with __span__ but no __iter__ is an error.
from tpy import Int32, Span, readonly_propagate

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    @readonly_propagate
    def __span__(self) -> Span[readonly_propagate[Int32]]:
        return self._data

def main() -> None:
    buf = Buffer()
    for x in buf:  # tpyc: error(/Cannot iterate over type Buffer/)
        print(x)

main()
