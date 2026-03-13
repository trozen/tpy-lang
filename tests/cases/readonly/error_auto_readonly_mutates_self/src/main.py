# Error: @auto_readonly method body may not mutate self.
# The const overload would be invalid C++ if self is mutated.
from tpy import Int32, Span, auto_readonly

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    @auto_readonly
    def bad_span(self) -> Span[Int32]:
        self._data.append(Int32(1))  # tpyc: error(/Cannot call non-readonly method/)
        return self._data
