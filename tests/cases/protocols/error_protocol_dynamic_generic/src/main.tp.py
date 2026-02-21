# Generic protocol cannot be @dynamic
from tpy import dynamic
from typing import Protocol

@dynamic
class Container[T](Protocol):  # tpyc: error(/@dynamic protocol 'Container' cannot be generic/)
    def get(self) -> T:
        ...
