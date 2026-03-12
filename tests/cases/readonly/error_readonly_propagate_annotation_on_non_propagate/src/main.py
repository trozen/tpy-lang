# Error: readonly_propagate[T] in return type is only valid on @readonly_propagate methods.
from tpy import Int32, Span, readonly_propagate

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    def as_span(self) -> Span[readonly_propagate[Int32]]:  # tpyc: error(/only allowed on @readonly_propagate/)
        return self._data
