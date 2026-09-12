from tpy import int32

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# ERROR: returning constructor creates temporary - dangling reference
def create_point(x: int32, y: int32) -> Point:
    return Point(x, y)  # tpyc: error(/Cannot return local or temporary/)
