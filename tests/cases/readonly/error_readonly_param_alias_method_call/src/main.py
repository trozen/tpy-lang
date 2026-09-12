# @readonly: local alias of param inherits readonly -- non-readonly method call rejected.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v

    def inc(self) -> None:
        self.value = self.value + 1


@readonly
def bad(b: Box) -> None:
    alias = b
    alias.inc()  # tpyc: error(/Cannot call non-readonly method 'inc' on readonly reference/)
