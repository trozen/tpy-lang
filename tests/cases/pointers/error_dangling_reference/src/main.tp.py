from tpy import Int32

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

# ERROR: returning constructor creates temporary - dangling reference
def create_point(x: Int32, y: Int32) -> Point:
    return Point(x, y)  # tpyc: error(/Cannot return local or temporary/)
