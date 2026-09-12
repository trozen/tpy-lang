# self: auto_readonly[Self] cannot be combined with @auto_readonly decorator.
from typing import Self
from tpy import int32, Span, auto_readonly

class Foo:
    _data: list[int32]
    def __init__(self) -> None:
        self._data = [int32(1)]

    @auto_readonly
    def get(self: auto_readonly[Self]) -> Span[auto_readonly[int32]]:  # tpyc: error(/cannot be combined with the @auto_readonly decorator/)
        return self._data
