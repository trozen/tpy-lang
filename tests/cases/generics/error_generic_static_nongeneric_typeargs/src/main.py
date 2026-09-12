# Error: type arguments on non-generic class static method call
from tpy import *

class C:
    value: int32
    def __init__(self, value: int32):
        self.value = value

    @staticmethod
    def create(x: int32) -> Own[C]:
        return C(x)

x = C[int32].create(1)  # tpyc: error(/not generic/)
