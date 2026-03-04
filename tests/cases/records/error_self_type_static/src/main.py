# Self type error: cannot use Self in @staticmethod
from typing import Self
from tpy import Int32

class Foo:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @staticmethod
    def bad_param(x: Self) -> None:  # tpyc: error(/Self type cannot be used.*@staticmethod/)
        pass
