# @override on a method in a class with no parent or protocol -- always an error.
from tpy import Int32
from typing import override

class Standalone:
    @override
    def do_thing(self) -> Int32:  # tpyc: error(/does not override/)
        return Int32(0)
