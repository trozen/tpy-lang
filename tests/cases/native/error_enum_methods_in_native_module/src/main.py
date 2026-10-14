# An enum with methods in a native module: the methods have bodies to emit,
# and a native module is declaration-only.
__tpy__ = {"mod": "native"}
from enum import Enum


class Color(Enum):  # tpyc: error(/methods on enum 'Color' not allowed in a native module/)
    Red = 0

    def label(self) -> str:
        return "red"
