from tpy import int32

__all__ = ["public_add", "Pair"]

def public_add(a: int32, b: int32) -> int32:
    return a + b

def not_exported(x: int32) -> int32:
    return x + int32(100)

class Pair:
    a: int32
    b: int32

    def __init__(self, a: int32, b: int32):
        self.a = a
        self.b = b
