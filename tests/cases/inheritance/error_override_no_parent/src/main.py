# @override on a method in a class with no parent or protocol -- always an error.
from tpy import int32
from typing import override

class Standalone:
    @override
    def do_thing(self) -> int32:  # tpyc: error(/does not override/)
        return int32(0)
