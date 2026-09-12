from tpy import int32

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

MAX_VALUE: int32 = int32(100)

def add(a: int32, b: int32) -> int32:
    return a + b
