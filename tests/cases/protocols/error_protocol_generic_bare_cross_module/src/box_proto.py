# Companion module: defines a user generic protocol imported bare by main.py.
from typing import Protocol
from tpy import int32


class BoxLike[T](Protocol):
    def get(self) -> T: ...
    def size(self) -> int32: ...
