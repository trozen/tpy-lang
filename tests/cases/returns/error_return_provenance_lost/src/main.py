from tpy import Int32


class Point:
    x: Int32
    y: Int32


def provenance_lost(points: list[Point]) -> Point:
    best = points[0]
    best = Point()
    return best  # tpyc: error(/Cannot return local or temporary/)
