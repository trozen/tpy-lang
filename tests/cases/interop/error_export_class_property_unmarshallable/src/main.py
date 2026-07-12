# A property whose getter return type isn't a boundary type is a located
# error naming the property (same admission as a method return).
# tpy: ext_module
from tpy.extern import export


@export
class Box:
    _b: bytearray

    def __init__(self) -> None:
        self._b = bytearray(b"xy")

    @property
    def buf(self) -> bytearray:  # tpyc: error(/property 'buf' of type 'bytearray' cannot cross/)
        return self._b
