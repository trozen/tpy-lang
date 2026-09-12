# @readonly: calling a non-readonly method directly on a param is rejected.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, v: int32):
        self.value = v

    def inc(self) -> None:
        self.value = self.value + 1


@readonly
def bad(b: Box) -> None:
    b.inc()  # tpyc: error(/Cannot call non-readonly method 'inc' on readonly reference/)
