from tpy import Int32, StaticList

class Point:
    x: Int32
    y: Int32

# ERROR: returning reference to element of local container
def bad_local_subscript() -> Point:
    local: StaticList[Point, 4] = StaticList[Point, 4]()
    local.append(Point())
    return local[0]  # tpyc: error(/Cannot return local or temporary/)
