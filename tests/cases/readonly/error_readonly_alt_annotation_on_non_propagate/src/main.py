# Error: readonly_alt[T] in return type is only valid on @readonly_alt methods.
from tpy import Int32, Span, readonly_alt

class Buffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = []

    def as_span(self) -> Span[readonly_alt[Int32]]:  # tpyc: error(/only allowed on @readonly_alt/)
        return self._data
