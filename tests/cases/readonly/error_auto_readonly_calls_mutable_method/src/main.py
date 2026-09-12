# Error: @auto_readonly method calling a non-readonly method on self.
from tpy import int32, Span, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = []

    def clear(self) -> None:
        self._data = []

    @auto_readonly
    def clear_and_span(self) -> Span[int32]:
        self.clear()  # tpyc: error(/Cannot call non-readonly method/)
        return self._data
