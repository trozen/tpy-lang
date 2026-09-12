"""Test that static methods cannot access 'self'."""
from tpy import int32

class Counter:
    value: int32

    def __init__(self, start: int32):
        self.value = start

    @staticmethod
    def bad_method() -> int32:
        return self.value  # tpyc: error(/Undefined variable/)
