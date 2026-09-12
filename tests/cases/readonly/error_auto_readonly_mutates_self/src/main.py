# Error: @auto_readonly method body may not mutate self.
# The const overload would be invalid C++ if self is mutated.
from tpy import int32, Span, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = []

    @auto_readonly
    def bad_span(self) -> Span[int32]:
        self._data.append(int32(1))  # tpyc: error(/Cannot call non-readonly method/)
        return self._data
