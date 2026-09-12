# Error: auto_readonly[T] in return type is only valid on @auto_readonly methods.
from tpy import int32, Span, auto_readonly

class Buffer:
    _data: list[int32]

    def __init__(self) -> None:
        self._data = []

    def as_span(self) -> Span[auto_readonly[int32]]:  # tpyc: error(/only allowed on @auto_readonly/)
        return self._data
