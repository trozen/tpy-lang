# Error: type arguments on non-generic class static method call
from tpy import *

class C:
    value: Int32
    def __init__(self, value: Int32):
        self.value = value

    @staticmethod
    def create(x: Int32) -> Own[C]:
        return C(x)

x = C[Int32].create(1)  # tpyc: error(/not generic/)
