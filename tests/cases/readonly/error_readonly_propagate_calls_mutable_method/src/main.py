# Error: @readonly_propagate method calling a non-readonly method on self.
from tpy import Int32, Span, readonly_propagate

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    def clear(self) -> None:
        self._data = []

    @readonly_propagate
    def clear_and_span(self) -> Span[Int32]:
        self.clear()  # tpyc: error(/Cannot call non-readonly method/)
        return self._data
