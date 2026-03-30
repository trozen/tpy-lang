from tpy import Int32

def double(x: Int32) -> Int32:
    return x + x

class Vec2:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y
