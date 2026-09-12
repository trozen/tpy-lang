from tpy import int32


class Point:
    x: int32
    y: int32


def mixed(points: list[Point], flag: bool) -> Point:
    local: Point = Point()
    if flag:
        result = points[0]
    else:
        result = local
    return result  # tpyc: error(/Cannot return local or temporary/)
