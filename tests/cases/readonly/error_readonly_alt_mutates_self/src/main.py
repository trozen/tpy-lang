# Error: @readonly_alt method body may not mutate self.
# The const overload would be invalid C++ if self is mutated.
from tpy import Int32, Span, readonly_alt

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    @readonly_alt
    def bad_span(self) -> Span[Int32]:
        self._data.append(Int32(1))  # tpyc: error(/Cannot call non-readonly method/)
        return self._data
