from tpy import Int32

__all__ = ["public_add", "Pair"]

def public_add(a: Int32, b: Int32) -> Int32:
    return a + b

def not_exported(x: Int32) -> Int32:
    return x + Int32(100)

class Pair:
    a: Int32
    b: Int32

    def __init__(self, a: Int32, b: Int32):
        self.a = a
        self.b = b
