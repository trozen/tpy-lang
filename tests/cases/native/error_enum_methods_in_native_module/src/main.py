# An enum with methods in a native_module: the methods have bodies to emit,
# and a native_module is declaration-only.
# tpy: native_module
from enum import Enum


class Color(Enum):  # tpyc: error(/methods on enum 'Color' not allowed in native_module/)
    Red = 0

    def label(self) -> str:
        return "red"
