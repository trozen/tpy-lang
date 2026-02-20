# Error: cannot infer type arguments for generic static method
# T does not appear in any argument position, so inference fails
from tpy import *

class Factory[T]:
    value: T
    def __init__(self, value: Own[T]):
        self.value = value

    @staticmethod
    def default_value() -> Own[Factory[T]]:
        return Factory(0)

x = Factory.default_value()  # tpyc: error(/Cannot infer type arguments/)
