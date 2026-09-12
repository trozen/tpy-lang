from tpy import int32

class Point:
    x: int32
    y: int32

# ERROR: returning reference to element of local container
def bad_local_subscript() -> Point:
    local: list[Point] = [Point()]
    return local[0]  # tpyc: error(/Cannot return local or temporary/)
