from tpy import Int32, Bool


class Point:
    x: Int32
    y: Int32


def mixed(points: list[Point], flag: Bool) -> Point:
    local: Point = Point()
    if flag:
        result = points[0]
    else:
        result = local
    return result  # tpyc: error(/Cannot return local or temporary/)
