from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

MAX_VALUE: Int32 = Int32(100)

def add(a: Int32, b: Int32) -> Int32:
    return a + b
