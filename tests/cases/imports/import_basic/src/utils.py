from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def add(a: Int32, b: Int32) -> Int32:
    return a + b
