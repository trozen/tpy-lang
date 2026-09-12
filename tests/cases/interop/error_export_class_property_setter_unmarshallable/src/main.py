# A property setter whose value type isn't a boundary type is a located
# error naming the setter value (the getter-return variant has its own case).
# tpy: ext_module
from tpy import int32
from tpy.extern import export


@export
class Box:
    _b: bytearray

    def __init__(self) -> None:
        self._b = bytearray(b"xy")

    @property
    def buf(self) -> int32:
        return len(self._b)

    @buf.setter
    def buf(self, b: bytearray) -> None:  # tpyc: error(/property 'buf' setter value of type 'bytearray' cannot cross/)
        self._b = b
