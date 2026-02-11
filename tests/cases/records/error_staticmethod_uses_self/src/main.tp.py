"""Test that static methods cannot access 'self'."""
from tpy import Int32

class Counter:
    value: Int32

    def __init__(self, start: Int32):
        self.value = start

    @staticmethod
    def bad_method() -> Int32:
        return self.value  # tpyc: error(/Undefined variable/)
