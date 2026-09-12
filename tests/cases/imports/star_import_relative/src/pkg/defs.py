from tpy import int32

def double(x: int32) -> int32:
    return x + x

class Vec2:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y
