# Error: wrong number of type arguments for generic static method call
from tpy import *

class Pair[T, U]:
    first: T
    second: U

    def __init__(self, first: Own[T], second: Own[U]):
        self.first = first
        self.second = second

    @staticmethod
    def create(a: Own[T], b: Own[U]) -> Own[Pair[T, U]]:
        return Pair(a, b)

x = Pair[Int32].create(1, 2)  # tpyc: error(/expects 2 type arguments, got 1/)
