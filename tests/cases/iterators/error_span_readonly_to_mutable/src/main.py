# Test that __span__() -> Span[readonly[T]] cannot coerce to mutable Span[T].
from tpy import Int32, Span, readonly

class IntBuffer:
    _data: list[Int32]

    def __init__(self) -> None:
        self._data = [1, 2, 3]

    def __span__(self) -> Span[readonly[Int32]]:
        return self._data

def mutate(s: Span[Int32]) -> None:
    s[0] = 99

buf = IntBuffer()
mutate(buf)  # tpyc: error(/Type mismatch/)
