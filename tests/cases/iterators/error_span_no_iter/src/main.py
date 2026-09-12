# Test that iterating over a type with __span__ but no __iter__ is an error.
from tpy import int32, Span, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    @auto_readonly
    def __span__(self) -> Span[auto_readonly[int32]]:
        return self._data

def main() -> None:
    buf = Buffer()
    for x in buf:  # tpyc: error(/Cannot iterate over type Buffer/)
        print(x)

main()
