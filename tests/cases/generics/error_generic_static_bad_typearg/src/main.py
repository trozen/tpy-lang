# Error: malformed type argument in generic static method call
from tpy import *

class G[T]:
    value: T
    def __init__(self, value: Own[T]):
        self.value = value

    @staticmethod
    def create(v: Own[T]) -> Own[G[T]]:
        return G(v)

x = G[123].create(1)  # tpyc: error(/not a valid type argument/)
