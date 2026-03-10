# @override on __init__ in a class with no parent should error -- no match anywhere.
from tpy import Int32
from typing import override

class Standalone:
    @override
    def __init__(self, x: Int32) -> None:  # tpyc: error(/does not override/)
        pass
